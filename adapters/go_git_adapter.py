#!/usr/bin/env python3
"""Level-2 adapter for go-git (`plumbing/format/gitignore`).

Why go-git belongs here. Level 2 exists because the tree layer is nobody's job: `pathspec`,
`gitignore_parser` and `node-ignore` match one file's worth of patterns, so the walk that decides
*which* `.gitignore` applies gets rewritten, untested, in every tool that needs it. go-git is one
of the few libraries that promises the layer outright -- `gitignore.ReadPatterns(fs, path)` reads
`.git/info/exclude` and then every `.gitignore` in the tree, and `NewMatcher(ps).Match(path,
isDir)` answers for any path -- and it is the git implementation that Gitea, ArgoCD and Flux
reach through, so the answer is load-bearing well past the library's own users.

It is a clean subject for the same two reasons dulwich is: the rule files keep the name
`.gitignore` (a renaming transport quietly changes the question -- that cost the 98th session
eight of nine "divergences" that were mine), and directory-ness is an explicit argument
(`Match(path, isDir)`) rather than something guessed from a trailing slash.

The measuring is split across two languages on purpose. This file writes the tree, exactly the
way every other adapter in this bench writes it, and `ggignore.go` -- built separately, see its
header -- does nothing but call go-git and answer. So the corpus, the oracle and the temporary
directories are shared code, and the only thing that changes between subjects is the library
under test.

Two entry points, because a library is not automatically one subject:

  * `--entry patterns` (default): `ReadPatterns` + `Matcher.Match`, the layer go-git promises.
  * `--entry status`: `Worktree.Status()`, which is the path Gitea and ArgoCD actually take.
    go-git calls `ReadPatterns` itself in `worktree_status.go`, so the two should agree, and
    "should" is why both are runnable. Directory queries are declined there, not guessed:
    `Status` reports files, and inventing a row for a directory would be my rule, not go-git's.

Where the binary comes from: `--helper <path>`, else `$GO_GIT_HELPER`, else a file named
`ggignore` next to this adapter. Its go-git version is printed to stderr at startup by the
binary itself -- do not take it on faith from a `go.mod` you did not build.

DECLINED (`null`, never guessed):
  * a query that could not be materialised on disk -- when one query is a parent directory of
    another that has to be a file. Same rule as every filesystem-citizen adapter here.
  * every directory query under `--entry status`, as above.

Usage:
  python3 gic.py --level 2 --corpus corpus/cases_l2.json -- \
      python3 adapters/go_git_adapter.py --helper /path/to/ggignore
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile


def _pop_option(argv, flag, env_var):
    """`--flag <value>` (or `--flag=<value>`), popped out of argv, else $<env_var>."""
    i = 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            del argv[i]
            return argv.pop(i)
        if argv[i].startswith(flag + "="):
            return argv.pop(i).split("=", 1)[1]
        i += 1
    return os.environ.get(env_var)


HELPER = (_pop_option(sys.argv, "--helper", "GO_GIT_HELPER")
          or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ggignore"))
ENTRY = (_pop_option(sys.argv, "--entry", "GO_GIT_ENTRY") or "patterns").lower()

if not os.path.exists(HELPER):
    sys.stderr.write(
        "no go-git helper at %s. Build adapters/ggignore.go (see its header) and pass\n"
        "--helper <path>, or set $GO_GIT_HELPER.\n" % HELPER)
    raise SystemExit(2)

HELPER_PROC = subprocess.Popen([HELPER, "--entry", ENTRY], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, bufsize=1)


def text_of(lines, what):
    """The wire says list-of-lines. Anything else is a protocol violation, so say so loudly.

    Not a hedge that accepts both shapes: accepting both is how the level-2 wire spent fifteen
    sessions sending newline-joined strings where PROTOCOL.md promised lists, with every test
    still green.
    """
    if not isinstance(lines, list):
        raise TypeError("%s must be a list of lines per PROTOCOL.md, got %s"
                        % (what, type(lines).__name__))
    return "\n".join(lines)


def build_tree(root, rules, exclude, queries):
    """Write the rule files and the queried paths. Returns the queries we could not create."""
    undecidable = set()

    for directory, lines in sorted(rules.items()):
        target = os.path.join(root, directory) if directory else root
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write(text_of(lines, "rules[%r]" % directory))

    # Longest first: if one query is a parent directory of another, the deeper one wins and the
    # shallower one can no longer be a file.
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

    # `.git` last, so a rule file or a query never fights with the control directory. Only
    # `info/exclude` is written here: `--entry patterns` needs no repository, and `--entry
    # status` runs `PlainInit` on the Go side, which is go-git's own idea of a repository rather
    # than one this adapter fabricates.
    if exclude:
        info = os.path.join(root, ".git", "info")
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "exclude"), "w", encoding="utf-8") as fh:
            fh.write(text_of(exclude, "exclude"))
    return undecidable


def ask_helper(request_id, root, queries, undecidable):
    payload = {"id": request_id, "root": root, "queries": queries,
               "declined": sorted(undecidable)}
    HELPER_PROC.stdin.write(json.dumps(payload) + "\n")
    HELPER_PROC.stdin.flush()
    line = HELPER_PROC.stdout.readline()
    if not line:
        raise SystemExit("go-git helper died")
    answer = json.loads(line)
    if answer.get("error"):
        raise SystemExit("go-git helper: %s" % answer["error"])
    return answer["ignored"]


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        # Level 1 is a level-2 tree with a single rule file at the root. Answering it costs
        # nothing and puts go-git's matcher next to its tree layer on the same corpus.
        rules, exclude = {"": request["patterns"]}, None

    root = tempfile.mkdtemp(prefix="gic-go-git-")
    try:
        undecidable = build_tree(root, rules, exclude, queries)
        return ask_helper(request["id"], root, queries, undecidable)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
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
