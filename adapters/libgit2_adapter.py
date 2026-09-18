#!/usr/bin/env python3
"""Level-2 adapter for libgit2 (`git_ignore_path_is_ignored`).

Why libgit2 belongs here, and why it is the strongest subject in the table. Level 2 exists because
the tree layer is nobody's job -- `pathspec` does not take a directory, and the eight lines that
walk the repo get rewritten in every tool. The subjects worth measuring are the ones that
*promise* the layer. dvc promises it. The `ignore` crate promises it. libgit2 does not merely
promise it: it promises to **be git**. `git_ignore_path_is_ignored()` is the same question
`git check-ignore` answers, with the same documented contract, written again in C by other people.

And it is infrastructure. pygit2, git2-rs, nodegit, GitHub Desktop and a long tail of things that
speak git without shelling out to git all get their answer from this function.

This is the shortest adapter in the repository, and that is the point: there is no eight-line
walk to write, no precedence to reimplement, no directory heuristic to guess. The subject exposes
the question directly, so the adapter's only job is to build the tree and ask.

The oracle stays what it is everywhere else: `git check-ignore --no-index` on the identical tree.

The helper it talks to is `adapters/lgignore.c`, sixty lines of C with the build line in its
header. Two settings on the subject's side, both of them putting the two into the *same* question
rather than shading the answer:

  * global / system / XDG config search paths are emptied, so libgit2 cannot read this machine's
    `core.excludesFile`. git is invoked with `--no-index` and reads no such thing either, and
    PROTOCOL.md puts machine state out of scope explicitly.
  * the repository is initialised by libgit2 itself, not by the `git` binary, so the subject sees
    exactly the tree the bench wrote.

`exclude` is supported rather than declined: libgit2 reads `.git/info/exclude` from a real
repository, which is what the tree here is, so `cases_l2_exclude.json` is a question it actually
offered to answer.

DECLINED (`null`, never guessed):
  * a query that could not be materialised (one query is a parent directory of another that needs
    to be a file). Same as every other adapter here.
  * a query the helper failed on -- reported on stderr, never silently turned into a verdict.

Two doors, because a corpus that asks about *prunability* ("does the tool descend into this
directory?") is not answered by `git_ignore_path_is_ignored`, which answers one path at a time and
never walks anything. `--entry api` (default) is that function. `--entry walk` is the door that
prunes, and libgit2 does expose it: `git_status_list_new` *without*
`GIT_STATUS_OPT_RECURSE_IGNORED_DIRS` promises that "the contents of ignored directories [are not]
included" (`include/git2/status.h`), so a pruned directory collapses into one entry with a trailing
slash. That collapse is the signal, and it is a promise of the public header rather than a reading
of the internal iterator. The helper for it is `adapters/lgwalk.c`.

Two things `--entry walk` has to do that `api` does not, both of them consequences of asking a
walk instead of a function:

  * a queried directory is created **with a sentinel file inside** (`_gic_probe`). An empty
    directory is invisible to any status-based walk -- git does not list one -- so without the
    sentinel every verdict would be `null` and the row would be a zero with nothing behind it.
  * the answer is read off the entry list: an entry that is exactly `d/` (or an ancestor of it that
    collapsed) means pruned; entries *below* `d/` mean descended; nothing at all is `null`.
  * that reading is *not enough on its own*, and the control is what proved it: an ignored `d/`
    entry also happens when the directory is perfectly visible and everything currently inside it
    is ignored -- and what is inside it is my own sentinel. So every candidate is re-asked on a
    second tree with `!_gic_probe` appended to that directory's own `.gitignore`. A re-inclusion
    cannot rescue a file whose parent directory is excluded, so: still collapsed -> really pruned;
    the sentinel shows up -> the walk was in there all along.

Whether that translation is faithful is not something to assert: `--walk-via git` runs the very
same protocol against the `git` binary's own `status --porcelain --ignored`, changing one line of
the pipeline. If the control does not score zero against the corpus, the signal is wrong and no
number from `--entry walk` means anything.

Where the binary comes from: `--bin <path>` on the command line, else `$LIBGIT2_ORACLE`, else a
`lgignore-main` next to this file (or `lgwalk-main` with `--entry walk`). Its libgit2 version goes to stderr on startup, because the
subject's version is measured, not assumed -- and *which* libgit2 you linked is the whole subject:
the numbers in the README are three builds of the same source commit that differ only by a patch.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

GITIGNORE = ".gitignore"
PROBE = "_gic_probe"  # sentinel inside a queried directory; see --entry walk in the docstring
PROBE_RULE = "!" + PROBE  # re-inclusion, appended to the candidate's own rules; see reinclusion()
AQUI = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(AQUI, "..", "..", "_scratch", "vendor_libgit2")


def _flag(argv, name, default, allowed=None):
    """`--name <v>` / `--name=<v>`, popped out of argv, else $GIC_<NAME>, else default."""
    val = None
    i = 0
    while i < len(argv):
        if argv[i] == name and i + 1 < len(argv):
            del argv[i]
            val = argv.pop(i)
            break
        if argv[i].startswith(name + "="):
            val = argv.pop(i).split("=", 1)[1]
            break
        i += 1
    if val is None:
        val = os.environ.get("GIC_" + name.lstrip("-").replace("-", "_").upper()) or default
    if allowed and val not in allowed:
        sys.exit("%s must be one of %s, got %r" % (name, "|".join(allowed), val))
    return val


ENTRY = _flag(sys.argv, "--entry", "api", ("api", "walk"))
# The control: same pipeline, the `git` binary in place of libgit2. See the docstring.
VIA = _flag(sys.argv, "--walk-via", "libgit2", ("libgit2", "git"))
# Next to this file first (that is what a clone will have), then my own build tree.
_STEM = "lgwalk" if ENTRY == "walk" else "lgignore"
DEFAULT_BINS = [os.path.join(AQUI, _STEM + "-main"),
                os.path.join(AQUI, _STEM),
                os.path.join(VENDOR, _STEM + "-main")]


def _bin_option(argv):
    """`--bin <path>`, else $LIBGIT2_ORACLE, else the first default that exists."""
    val = _flag(argv, "--bin", None)
    if val:
        return val
    if os.environ.get("LIBGIT2_ORACLE"):
        return os.environ["LIBGIT2_ORACLE"]
    for cand in DEFAULT_BINS:
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return os.path.abspath(DEFAULT_BINS[0])


BIN = _bin_option(sys.argv)


def text_of(lines, what):
    """The wire says list-of-lines; anything else is a protocol violation, said out loud.

    No isinstance hedge on purpose -- accepting both shapes is what let the bench send strings for
    fifteen sessions with every test green.
    """
    if not isinstance(lines, list):
        raise TypeError("%s must be a list of lines per PROTOCOL.md, got %s"
                        % (what, type(lines).__name__))
    return "\n".join(lines)


def build_tree(root, rules, exclude, queries, probe_dirs=()):
    """Write the rule files and the queried paths. Returns what could not be created.

    `probe_dirs` is the re-inclusion probe: each of those directories gets `!_gic_probe` appended
    to its *own* `.gitignore` (deepest file wins, last matching pattern wins), so the sentinel is
    re-included wherever the walk is allowed to look at it at all.
    """
    undecidable = set()

    for directory, lines in sorted(rules.items()):
        target = os.path.join(root, directory) if directory else root
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, GITIGNORE), "w", encoding="utf-8") as fh:
            fh.write(text_of(lines, "rules[%r]" % directory))

    if exclude is not None:
        info = os.path.join(root, ".git", "info")
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "exclude"), "w", encoding="utf-8") as fh:
            fh.write(text_of(exclude, "exclude"))

    # Longest first: a query that is another query's parent directory has to stay a directory.
    for query in sorted(queries, key=len, reverse=True):
        full = os.path.join(root, query.rstrip("/"))
        try:
            if query.endswith("/"):
                os.makedirs(full, exist_ok=True)
                if ENTRY == "walk":
                    # An empty directory is invisible to a status-based walk. The sentinel is what
                    # makes "did it descend here?" an observable question at all.
                    with open(os.path.join(full, PROBE), "w", encoding="utf-8") as fh:
                        fh.write("x\n")
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if os.path.isdir(full):
                undecidable.add(query)
                continue
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        except OSError:
            undecidable.add(query)

    for directory in probe_dirs:
        target = os.path.join(root, directory.rstrip("/"))
        if not os.path.isdir(target):
            continue
        path = os.path.join(target, GITIGNORE)
        previo = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                previo = fh.read()
            if previo and not previo.endswith("\n"):
                previo += "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(previo + PROBE_RULE + "\n")
    return undecidable


def ask(root, queries):
    """One process per tree. Returns a list of 1/0/None, aligned with `queries`."""
    proc = subprocess.run([BIN, root], input="\n".join(queries) + "\n",
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        sys.stderr.write("lgignore exited %d: %s\n" % (proc.returncode, proc.stderr[-400:]))
        return [None] * len(queries)

    lines = [l for l in proc.stdout.split("\n") if l != ""]
    # Cardinality, not just content: a short reply means the alignment is gone, and a
    # misaligned verdict is worse than no verdict.
    if len(lines) != len(queries):
        sys.stderr.write("lgignore returned %d lines for %d queries\n" % (len(lines), len(queries)))
        return [None] * len(queries)

    out = []
    for query, line in zip(queries, lines):
        if line == "1":
            out.append(True)
        elif line == "0":
            out.append(False)
        else:
            sys.stderr.write("lgignore: %s -> %s\n" % (query, line))
            out.append(None)
    return out


GIT_STATUS_IGNORED = 0x4000


def entries(root):
    """The walk's entry list: `{path: ignored?}`, directories keeping their trailing slash.

    `None` if the subject failed -- a crash is not an answer. The *flag* is half the reading, not
    decoration: with `--untracked-files=normal` a plain untracked directory also collapses into one
    entry, and telling "collapsed because pruned" from "collapsed because listed whole" is exactly
    the difference between the two verdicts.
    """
    if VIA == "git":
        subprocess.run(["git", "init", "-q", root], capture_output=True, text=True, timeout=120)
        proc = subprocess.run(["git", "-C", root, "status", "--porcelain",
                               "--untracked-files=normal", "--ignored=traditional"],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            sys.stderr.write("git status exited %d: %s\n" % (proc.returncode, proc.stderr[-400:]))
            return None
        out = {}
        for line in proc.stdout.split("\n"):
            if len(line) > 3:
                path = line[3:].strip()
                if path.startswith('"') and path.endswith('"'):
                    path = path[1:-1]
                out[path] = line[:2] == "!!"
        return out

    proc = subprocess.run([BIN, root], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not proc.stdout.rstrip().endswith("OK"):
        sys.stderr.write("lgwalk exited %d: %s\n" % (proc.returncode, proc.stderr[-400:]))
        return None
    out = {}
    for line in proc.stdout.split("\n"):
        if line and line != "OK" and " " in line:
            flags, path = line.split(" ", 1)
            out[path] = bool(int(flags, 16) & GIT_STATUS_IGNORED)
    return out


def ask_walk(root, queries, undecidable):
    """Prunable == the walk collapsed this directory (or an ancestor) into one *ignored* entry.

    Read in this order, and the order is the whole thing:
      1. the entry covering the query -- itself, or the nearest collapsed ancestor. Ignored means
         pruned; untracked means the walk got there and merely abbreviated its output.
      2. failing that, anything listed *below* the query: it descended.
      3. failing that, no evidence at all -- `null`, never a guess.
    """
    seen = entries(root)
    if seen is None:
        return [None] * len(queries)

    out = []
    for query in queries:
        if query in undecidable or not query.endswith("/"):
            out.append(None)
            continue
        if not os.path.isdir(os.path.join(root, query.rstrip("/"))):
            out.append(None)
            continue
        cubre = [e for e in seen
                 if e.endswith("/") and (e == query or query.startswith(e))]
        if cubre:
            out.append(seen[max(cubre, key=len)])
        elif any(e.startswith(query) for e in seen):
            out.append(False)
        else:
            out.append(None)
    return out


def reinclusion(rules, exclude, queries, verdicts):
    """Second pass over the candidates: is that collapsed entry a closed door, or a full bin?

    Only the queries answered `True` need it -- a walk that already descended cannot be talked out
    of it -- so the cost is one extra tree per case that has any candidate at all, and none for the
    970-odd that have none.
    """
    cand = {q for q, v in zip(queries, verdicts) if v is True}
    if not cand:
        return verdicts

    root = tempfile.mkdtemp(prefix="gic_libgit2_probe_")
    try:
        build_tree(root, rules, exclude, queries, probe_dirs=cand)
        seen = entries(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if seen is None:
        return [None if q in cand else v for q, v in zip(queries, verdicts)]
    return [(not any(e.startswith(q) and e != q for e in seen)) if q in cand else v
            for q, v in zip(queries, verdicts)]


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) != 2:
        # Level 1 hands over patterns with no tree. libgit2 answers about a repository, so the
        # honest reply is to decline rather than to fabricate a one-file repo.
        return [None] * len(queries)

    rules = request.get("rules") or {}
    exclude = request.get("exclude")

    root = tempfile.mkdtemp(prefix="gic_libgit2_")
    try:
        undecidable = build_tree(root, rules, exclude, queries)
        if ENTRY == "walk":
            return reinclusion(rules, exclude, queries,
                               ask_walk(root, queries, undecidable))
        verdicts = ask(root, queries)
        return [None if q in undecidable else v for q, v in zip(queries, verdicts)]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    if VIA != "git" and not os.path.exists(BIN):
        sys.exit("libgit2 helper not found: %s -- build adapters/lgignore.c (its header has the "
                 "cmake and cc lines) and pass it with --bin or $LIBGIT2_ORACLE" % BIN)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        sys.stdout.write(json.dumps({"id": request["id"],
                                     "ignored": answer(request)}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
