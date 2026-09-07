#!/usr/bin/env python3
"""Level-2 adapter for BurntSushi/ripgrep (the `ignore` crate).

Why ripgrep belongs here. Level 2 measures the tree layer -- who reads which `.gitignore` and in
what order -- and that layer is normally nobody's job, written fresh in eight lines by every tool
that walks a repo. dvc was the first subject I found that *promises* it. ripgrep is the second,
and it promises it harder than anyone: nested `.gitignore` files with correct scoping and
precedence is a headline feature, not an incidental. The `ignore` crate it is built on is also
what `fd`, `ruff` and a good part of the Rust tooling world walk with, so this one number is asked
of a lot of installed software at once.

**The subject is the official binary**, downloaded from upstream's release page by
`_scratch/_m100_vendor_rg.py` (14.1.1, rev 4649aa9700). The `rg` already on my VPS is a shell
function wrapping a different build with a different rev; measuring that and calling it ripgrep
would put my own harness under test instead of the crate.

## How you ask a walker "is this ignored?"

You don't -- ripgrep has no such entry point, and inventing one would be measuring something else.
It answers "what would you walk", which is the same question from the other side, and is in fact
the question level 2 is about. Two channels are read and cross-checked:

* `--files` lists the paths it would search. A materialised file missing from that list is
  ignored.
* `--debug` prints one line per skipped entry -- `ignoring ./build: ...` -- and those lines cover
  **directories** too, which `--files` never can. That is what makes directory queries answerable
  without planting a sentinel file inside the directory; a sentinel would change the tree being
  measured, and the query would start fabricating its own answer.

A pruned directory is never descended into, so nothing under it is ever reported. The verdict for
a path is therefore: *itself or any ancestor appears in the skip set, and it is not whitelisted*.
For file queries the two channels must agree; if they don't, the adapter says so on stderr and
answers `null` rather than picking the one it likes.

## Flags, and why each one is not cheating

ripgrep's defaults answer a slightly different question than git's, so three flags exist to put
the two on the same question -- not to flatter ripgrep:

* `--hidden`. Without it ripgrep skips every dotfile *as a matter of its own policy*, which has
  nothing to do with gitignore. Measured: on a tree whose only gitignore rule was `*.log`, the
  default run reported `.gitignore`, `.visible` and every other dotfile as ignored. That would be
  a hundred fake divergences, all mine.
* `--no-ignore-dot`. ripgrep also honours `.ignore` and `.rgignore`. In this corpus those names
  can appear as *queried paths*, and one of them did: a `.ignore` file whose contents were being
  queried got read as a rule file and swallowed `src/main.c`. Same class of self-inflicted wound
  as the `.dvcignore` rename in the 98th session -- caught here by probing instead of by luck.
* `--no-ignore-global`, `--no-ignore-parent`, `--no-require-git`. Nothing outside the temporary
  tree may vote: not this machine's `core.excludesFile`, not a `.gitignore` above `/tmp`. And no
  `git init` is run, so what is measured is the crate's own tree layer rather than a git repo's.

DECLINED (`null`, never guessed):
  * any request carrying `exclude`. `.git/info/exclude` is read by ripgrep only inside a real git
    repository, and `cases_l2_exclude.json` is a separate scorecard for a reason.
  * queries under `.git/`. ripgrep hard-codes that one; it is not a gitignore verdict.
  * a query that could not be materialised (one query is a parent directory of another that must
    be a file).
  * any query whose two channels disagree, or any case where ripgrep exits non-zero.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

GITIGNORE = ".gitignore"
DEFAULT_RG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "_scratch", "vendor_rg", "rg")

# `rg: DEBUG|ignore::walk|crates/ignore/src/walk.rs:1799: ignoring ./build: Ignore(...)`
SKIP = re.compile(r":\s+(ignoring|whitelisting)\s+(\./)?(?P<path>.*?):\s")

FLAGS = ["--files", "--hidden", "--no-ignore-dot", "--no-ignore-global",
         "--no-ignore-parent", "--no-require-git", "--debug"]


def _src_option(argv, env_var):
    """`--src <binario>` (or `--src=<binario>`), popped out of argv, else $<env_var>."""
    i = 0
    while i < len(argv):
        if argv[i] == "--src" and i + 1 < len(argv):
            del argv[i]
            return argv.pop(i)
        if argv[i].startswith("--src="):
            return argv.pop(i).split("=", 1)[1]
        i += 1
    return os.environ.get(env_var)


RG = _src_option(sys.argv, "RIPGREP_BIN") or DEFAULT_RG
if not os.path.exists(RG):
    sys.stderr.write("no ripgrep binary at %s -- run `_scratch/_m100_vendor_rg.py`, or pass\n"
                     "`--src /path/to/rg`.\n" % RG)
    raise SystemExit(2)


def text_of(lines, what):
    """The wire says list-of-lines; anything else is a protocol violation, said out loud.

    No isinstance hedge on purpose -- see the dvc adapter; accepting both shapes is what let the
    bench send strings for fifteen sessions with every test green.
    """
    if not isinstance(lines, list):
        raise TypeError("%s must be a list of lines per PROTOCOL.md, got %s"
                        % (what, type(lines).__name__))
    return "\n".join(lines)


def build_tree(root, rules, queries):
    """Write the `.gitignore` files and the queried paths. Returns what could not be created."""
    undecidable = set()

    for directory, lines in sorted(rules.items()):
        target = os.path.join(root, directory) if directory else root
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, GITIGNORE), "w", encoding="utf-8") as fh:
            fh.write(text_of(lines, "rules[%r]" % directory))

    # Longest first: a query that is another query's parent directory has to stay a directory.
    for query in sorted(queries, key=len, reverse=True):
        full = os.path.join(root, query.rstrip("/"))
        try:
            if query.endswith("/"):
                os.makedirs(full, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if os.path.isdir(full):
                undecidable.add(query)
                continue
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        except OSError:
            undecidable.add(query)
    return undecidable


def run_ripgrep(root):
    """Returns (walked files, skipped paths, whitelisted paths) or None if ripgrep failed."""
    proc = subprocess.run([RG] + FLAGS, cwd=root, capture_output=True, text=True, timeout=120)
    # rc=1 just means "no files matched"; anything above that is a real failure.
    if proc.returncode > 1:
        sys.stderr.write("ripgrep exited %d: %s\n" % (proc.returncode, proc.stderr[-400:]))
        return None
    # `removeprefix`, not `lstrip`: lstrip takes a character SET, so `.lstrip("./")` turns
    # `.next` into `next` and `.pnp.js` into `pnp.js`. That bug shipped for exactly one bench run
    # and the cross-check caught all 72 of its victims, which is the whole reason it is here.
    walked = {l[2:] if l.startswith("./") else l for l in proc.stdout.split("\n") if l.strip()}
    skipped, whitelisted = set(), set()
    for line in proc.stderr.split("\n"):
        m = SKIP.search(line)
        if not m:
            continue
        (whitelisted if m.group(1) == "whitelisting" else skipped).add(m.group("path"))
    return walked, skipped, whitelisted


def _ancestors(path):
    """`a/b/c` -> `a/b/c`, `a/b`, `a`. A pruned directory is never descended into, so the only
    evidence for something underneath it is the skip line on the ancestor itself."""
    parts = path.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        yield "/".join(parts[:i])


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        rules, exclude = {"": request["patterns"]}, None

    if exclude:
        return [None] * len(queries)

    root = tempfile.mkdtemp(prefix="gic-rg-")
    try:
        undecidable = build_tree(root, rules, queries)
        result = run_ripgrep(root)
        if result is None:
            return [None] * len(queries)
        walked, skipped, whitelisted = result

        out = []
        for query in queries:
            bare = query.rstrip("/")
            if query in undecidable or bare.split("/")[0] == ".git":
                out.append(None)
                continue

            verdict = None
            for anc in _ancestors(bare):
                if anc in whitelisted:
                    verdict = False
                    break
                if anc in skipped:
                    verdict = True
                    break
            if verdict is None:
                verdict = False

            # Second channel, for files only: `--files` is the list ripgrep would actually
            # search. The two must agree; a disagreement means I read the debug output wrong,
            # and a wrong reading of my own harness must not be scored as the subject's bug.
            if not query.endswith("/"):
                by_walk = bare not in walked
                if by_walk != verdict:
                    sys.stderr.write("channels disagree on %r (request %s): debug=%s --files=%s\n"
                                     % (query, request.get("id"), verdict, by_walk))
                    out.append(None)
                    continue
            out.append(verdict)
        return out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        sys.stdout.write(json.dumps({"id": request["id"], "ignored": answer(request)}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
