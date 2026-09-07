#!/usr/bin/env python3
"""Level-2 adapter for sharkdp/fd.

Why fd, when ripgrep is already scored. Because they are not two subjects -- they are one. Both
walk with BurntSushi's `ignore` crate, and level 2 asks precisely about the layer that crate owns:
which `.gitignore` files get read, in what scope, in what precedence. Scoring fd separately turns
"ripgrep has a bug" into "the crate a large slice of the Rust tooling world walks with has a bug",
which is a different claim with a different audience. If the two scorecards come out identical,
that identity *is* the finding. If they don't, the difference is where the crate ends and each
binary's own packaging begins -- also worth knowing, and not guessable from reading the source.

**The subject is the official binary** from upstream's release page, fetched by
`_scratch/_m101_vendor.py` (fd 10.5.0, x86_64-unknown-linux-musl). Same care as with ripgrep: what
is in a machine's PATH is not necessarily the thing you think you are measuring.

## How you ask fd "is this ignored?"

Same way as ripgrep: you don't, you ask what it would walk. fd has no `--debug`, so the two
cross-checked channels are different ones -- and they turn out to be better suited to level 2:

* **restricted walk** -- `fd --hidden --no-ignore-parent --no-require-git`, listing files *and*
  directories. fd prints directories with a trailing slash (`sub/`), which is exactly the shape
  the corpus uses for directory queries. So directory verdicts need no sentinel file planted
  inside them; ripgrep needed `--debug` for that, and a sentinel would have changed the tree being
  measured.
* **unrestricted walk** -- the same command plus `--no-ignore`. This is the existence channel: it
  says what is actually on disk. A query missing from *both* walks was never materialised, and
  that is my harness failing, not fd ignoring anything. Scoring it as a divergence would be the
  third leg of attribution collapsing.

Verdict: present in the unrestricted walk, absent from the restricted one.

## Flags, and why each one is not cheating

* `--hidden`. Without it fd hides every dotfile by its own policy, unrelated to gitignore --
  `.gitignore` itself included. Measured on ripgrep in the 100th session; same policy here.
* `--no-require-git`. The bench materialises bare trees, with no `git init`. Without this flag fd
  ignores `.gitignore` files entirely outside a repository and scores 0 answers, which measures
  nothing.
* `--no-ignore-parent`. Nothing above the temporary tree gets a vote.
* NOT `--no-ignore-vcs` and NOT `--no-ignore`: those switch off the very thing under test.

DECLINED (`null`, never guessed):
  * any request carrying `exclude` -- `.git/info/exclude` needs a real repository, and it has its
    own scorecard.
  * any tree where a *queried path* is named `.ignore`, `.fdignore` or `.rgignore`. fd reads those
    as rule files and has no flag to stop it (ripgrep has `--no-ignore-dot`; fd does not). The
    file the bench materialises would be read as a pattern list, and the query would fabricate its
    own answer -- the `.dvcignore` wound of the 98th session, declined instead of suffered.
  * queries under `.git/`, and anything that could not be materialised.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_FD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "_scratch", "vendor_fd", "fd")
GITIGNORE = ".gitignore"

BASE = ["--hidden", "--no-ignore-parent", "--no-require-git",
        "--strip-cwd-prefix=always", "--color=never"]

# Names fd reads as rule files no matter what. If one of them is a query, this tree is undecidable.
RULE_NAMES = {".ignore", ".fdignore", ".rgignore"}

def _src_option(argv, env_var):
    """`--src <binario>` (or `--src=<binario>`), popped out of argv, else $<env_var>.

    Deliberately not imported from `ripgrep_adapter`: importing it runs its module body, which
    pops `--src` for *its* binary and exits if no `rg` is around. A shared helper that quietly
    eats another adapter's argument is exactly the kind of harness bug that gets scored as the
    subject's.
    """
    i = 0
    while i < len(argv):
        if argv[i] == "--src" and i + 1 < len(argv):
            del argv[i]
            return argv.pop(i)
        if argv[i].startswith("--src="):
            return argv.pop(i).split("=", 1)[1]
        i += 1
    return os.environ.get(env_var)


def text_of(lines, what):
    """The wire says list-of-lines; anything else is a protocol violation, said out loud."""
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


FD = _src_option(sys.argv, "FD_BIN") or DEFAULT_FD
if not os.path.exists(FD):
    sys.stderr.write("no fd binary at %s -- run `_scratch/_m101_vendor.py`, or pass "
                     "`--src /path/to/fd`.\n" % FD)
    raise SystemExit(2)


def walk(root, unrestricted):
    """The set of paths fd reports. Directories keep their trailing slash."""
    cmd = [FD] + BASE + (["--no-ignore"] if unrestricted else [])
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120)
    if proc.returncode > 1:
        sys.stderr.write("fd exited %d: %s\n" % (proc.returncode, proc.stderr[-400:]))
        return None
    return {line for line in proc.stdout.split("\n") if line.strip()}


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        rules, exclude = {"": request["patterns"]}, None

    if exclude:
        return [None] * len(queries)
    if any(os.path.basename(q.rstrip("/")) in RULE_NAMES for q in queries):
        return [None] * len(queries)

    root = tempfile.mkdtemp(prefix="gic-fd-")
    try:
        undecidable = build_tree(root, rules, queries)
        restricted, everything = walk(root, False), walk(root, True)
        if restricted is None or everything is None:
            return [None] * len(queries)

        out = []
        for query in queries:
            bare = query.rstrip("/")
            if query in undecidable or bare.split("/")[0] == ".git":
                out.append(None)
                continue
            # fd prints a directory as `d/` and a file as `d`; ask for the shape the corpus used,
            # and let the existence channel decide whether the miss is fd's or mine.
            shape = bare + "/" if query.endswith("/") else bare
            if shape not in everything:
                sys.stderr.write("not materialised: %r (request %s)\n" % (query, request.get("id")))
                out.append(None)
                continue
            out.append(shape not in restricted)
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
