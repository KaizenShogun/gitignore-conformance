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

Where the binary comes from: `--bin <path>` on the command line, else `$LIBGIT2_ORACLE`, else a
`lgignore-main` next to this file. Its libgit2 version goes to stderr on startup, because the
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
AQUI = os.path.dirname(os.path.abspath(__file__))
# Next to this file first (that is what a clone will have), then my own build tree.
DEFAULT_BINS = [os.path.join(AQUI, "lgignore-main"),
                os.path.join(AQUI, "lgignore"),
                os.path.join(AQUI, "..", "..", "_scratch", "vendor_libgit2", "lgignore-main")]


def _bin_option(argv):
    """`--bin <path>` (or `--bin=<path>`), popped out of argv, else $LIBGIT2_ORACLE, else default."""
    i = 0
    while i < len(argv):
        if argv[i] == "--bin" and i + 1 < len(argv):
            del argv[i]
            return argv.pop(i)
        if argv[i].startswith("--bin="):
            return argv.pop(i).split("=", 1)[1]
        i += 1
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


def build_tree(root, rules, exclude, queries):
    """Write the rule files and the queried paths. Returns what could not be created."""
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
        verdicts = ask(root, queries)
        return [None if q in undecidable else v for q, v in zip(queries, verdicts)]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    if not os.path.exists(BIN):
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
