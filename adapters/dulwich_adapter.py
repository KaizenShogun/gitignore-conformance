#!/usr/bin/env python3
"""Level-2 adapter for dulwich (`dulwich.ignore.IgnoreFilterManager`).

Why dulwich belongs here. Level 2 exists because the tree layer is nobody's job: `pathspec`,
`gitignore_parser` and `node-ignore` match one file's worth of patterns and none of them takes a
directory, so the walk that decides *which* `.gitignore` applies gets rewritten, untested, in
every tool. dulwich is one of the few that promises the layer outright -- `IgnoreFilterManager`
loads a `.gitignore` per directory on demand, stacks `.git/info/exclude` and the user's global
excludes underneath, and answers "is this path ignored?" for a whole repository. It is also a
subject with real downstream users: DVC's git backend, breezy, and anything else that reaches for
a git implementation in pure Python.

Two things make dulwich the cleanest subject in this bench so far:

  * **The rule files keep their name.** dvc reads `.dvcignore`, so its adapter has to rename every
    rule file on the way in, and a corpus pattern that talks about `.gitignore` stops talking
    about the rule file. That cost the 98th session eight of nine "divergences" that were mine.
    Here the file on disk is a `.gitignore`, which is what the corpus says it is.
  * **Directory-ness is in the API.** `is_ignored()` documents that a directory path should end
    with `/` -- the same convention PROTOCOL.md uses, and the same one `git check-ignore` uses.
    Nothing is stripped, guessed or reconstructed on the way in.

Measured against the same oracle as everything else here: `git check-ignore --no-index` (git
2.55.0) over the identical tree.

`None` is not a decline. `is_ignored()` returns `None` for "no pattern mentions this path", which
in git's terms is a decided, ordinary "not ignored" -- and it is how dulwich itself consumes the
answer (`porcelain.check_ignore` does `if ignore_manager.is_ignored(path):`, so `None` is falsy
and the path is not reported). Mapping it to `null` would hide real answers behind an honest-
looking gap. It is mapped to `False`.

DECLINED (`null`, never guessed):
  * a query that could not be materialised on disk -- when one query is a parent directory of
    another that has to be a file. Same rule as every filesystem-citizen adapter here.

Two entry points, because a library is not automatically one subject. `--entry api` (default) asks
`IgnoreFilterManager.is_ignored` directly; `--entry porcelain` goes through
`porcelain.check_ignore(..., no_index=True)`, which is dulwich's own `git check-ignore` and does
its own directory-slash handling on the way. They should agree on this corpus -- the trees carry
no index and the queries already carry their slashes -- and "should" is why both are runnable.

Where the code comes from: `--src <dir>` on this adapter's command line, else `$DULWICH_SRC`,
else the installed package. The version is printed to stderr at startup; do not take it on faith
from a requirements file.

Usage:
  python3 gic.py --level 2 --corpus corpus/cases_l2.json -- \
      python3 adapters/dulwich_adapter.py --src /path/to/dulwich/parent
"""
import json
import os
import shutil
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


def _load_dulwich():
    src = _pop_option(sys.argv, "--src", "DULWICH_SRC")
    if src:
        sys.path.insert(0, src)
    try:
        import dulwich
        from dulwich.ignore import IgnoreFilterManager
        from dulwich.repo import Repo
    except ImportError as exc:
        sys.stderr.write(
            "cannot import dulwich (%s). `pip install dulwich`, or point --src/$DULWICH_SRC at\n"
            "a directory containing the dulwich package.\n" % exc)
        raise SystemExit(2)
    version = ".".join(str(p) for p in dulwich.__version__)
    sys.stderr.write("dulwich %s from %s\n" % (version, os.path.dirname(dulwich.__file__)))
    return IgnoreFilterManager, Repo


ENTRY = (_pop_option(sys.argv, "--entry", "DULWICH_ENTRY") or "api").lower()
if ENTRY not in ("api", "porcelain"):
    sys.stderr.write("--entry must be 'api' or 'porcelain', got %r\n" % ENTRY)
    raise SystemExit(2)

IGNORE_FILTER_MANAGER, REPO = _load_dulwich()
if ENTRY == "porcelain":
    from dulwich import porcelain  # noqa: E402  (after the --src path is in place)


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

    # `.git` last, so a rule file or a query never fights with the control directory. dulwich's
    # own `Repo.init` writes it -- no `git` binary is involved anywhere in this adapter.
    repo = REPO.init(root)
    if exclude:
        info = os.path.join(repo.controldir(), "info")
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "exclude"), "w", encoding="utf-8") as fh:
            fh.write(text_of(exclude, "exclude"))
    return repo, undecidable


def ask_api(repo, queries, undecidable):
    """`IgnoreFilterManager.is_ignored`, the entry point downstream users hold."""
    manager = IGNORE_FILTER_MANAGER.from_repo(repo)
    out = []
    for query in queries:
        if query in undecidable:
            out.append(None)
            continue
        verdict = manager.is_ignored(query)
        # None means "no pattern mentions it", which is git's plain "not ignored".
        out.append(False if verdict is None else bool(verdict))
    return out


def ask_porcelain(repo, queries, undecidable):
    """`dulwich check-ignore`, which is what a user at a prompt would run."""
    askable = [q for q in queries if q not in undecidable]
    reported = set(porcelain.check_ignore(repo, askable, no_index=True, quote_path=False))
    return [None if q in undecidable else (q in reported) for q in queries]


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        # Level 1 is a level-2 tree with a single rule file at the root. Answering it costs
        # nothing and puts dulwich's matcher next to its tree layer on the same corpus.
        rules, exclude = {"": request["patterns"]}, None

    root = tempfile.mkdtemp(prefix="gic-dulwich-")
    try:
        repo, undecidable = build_tree(root, rules, exclude, queries)
        try:
            if ENTRY == "api":
                return ask_api(repo, queries, undecidable)
            return ask_porcelain(repo, queries, undecidable)
        finally:
            repo.close()
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
