#!/usr/bin/env python3
"""Level-2 adapter for iterative/dvc (`.dvcignore`).

Why dvc belongs here. Level 2 exists because the tree layer is nobody's job: `pathspec` does not
take a directory, and the eight lines that walk the repo get written fresh in every tool. dvc is
the exception I went looking for -- it *promises* the layer in its documentation (a `.dvcignore`
per directory, "the same syntax as .gitignore") and it has users, which the three implementations
I read in the 96th session did not. `DvcIgnoreFilter` keeps a `pygtrie` of directory -> merged
pattern list, rewrites a child file's patterns onto the parent's prefix (`merge_patterns`), scans
matches in reverse so the last one wins, and walks a path's ancestors so an excluded directory
cannot be re-included from below. That is the whole shape of the problem, written by the subject.

What it is measured against is git, same as everything else here: the corpus's rule files are
written out as `.dvcignore` and the oracle is `git check-ignore --no-index` on the identical tree.
dvc documents gitignore syntax, so the comparison is fair -- but only for what it promises.

DECLINED (`null`, never guessed):
  * any request carrying `exclude`. `.git/info/exclude` is a git file; dvc never claimed to read
    it, and scoring a tool on a promise it did not make is how you get a dishonest number.
  * queries under `.git/`, `.hg/` or `.dvc/`. dvc hard-codes those four patterns at the root of
    the trie for its own reasons. That is a different feature, not a gitignore verdict.
  * a query that could not be materialised (one query is a parent directory of another that
    needs to be a file).
  * any case whose verdicts depend on the rule files being *readable*. This one is my fault, not
    dvc's, and it cost the 98th session to find: the rule files get renamed on the way in, so a
    pattern that talks about the rule file stops talking about the rule file.
    `cpburnz/python-pathspec` has `.*` followed by `!.gitignore` at the root -- after the rename
    `dev/.dvcignore` falls under `.*` with nothing re-including it, and dvc (unlike git) will not
    read a rule file that its ancestors ignore, so `dev`'s two patterns silently never applied.
    That was 8 of the 9 divergences I was about to blame on dvc. It is detected, not guessed: the
    case is answered a second time with `!.dvcignore` appended at the root, and only a case whose
    answers *move* is declined. The probe is a detector; the reported answer always comes from
    the corpus's rules verbatim.

Like black's adapter, dvc is a filesystem citizen: it reads rule files off disk and asks about
real paths, so each request gets a temporary tree. Unlike black's, no walk is needed -- dvc has a
public "is this ignored?" entry point (`is_ignored_file` / `is_ignored_dir`, the two the local
filesystem itself calls) and it is asked exactly that, with dvc's own defaults.

Two entry points, because a corpus that asks about *prunability* ("does the tool descend into this
directory?") is not answered by every door. `--entry api` (default) asks `is_ignored_dir` /
`is_ignored_file`. `--entry walk` runs the real thing -- `DvcIgnoreFilter.walk(localfs, root)`, the
generator `dvc.fs` uses -- and answers "prunable" for any directory the walk never reaches. Unlike
dulwich, dvc has no separate prune door: `DvcIgnorePatterns.__call__` filters a walk's `dirs` with
`self.matches(root, d, True)`, the same predicate `is_ignored_dir` ends in. The difference the two
entries can still show is *ancestors*: `api` answers each path on its own, the walk never reaches a
child of a pruned parent. Which one matches git is a measurement, not a reading.

Where the code comes from: `--src <dir>` on this adapter's command line, else `$DVC_SRC`, else the
installed package.
"""
import json
import os
import shutil
import sys
import tempfile

DVC_OWN = (".git", ".hg", ".dvc")
DVCIGNORE = ".dvcignore"


def _src_option(argv, env_var):
    """`--src <dir>` (or `--src=<dir>`), popped out of argv, else $<env_var>."""
    i = 0
    while i < len(argv):
        if argv[i] == "--src" and i + 1 < len(argv):
            del argv[i]
            return argv.pop(i)
        if argv[i].startswith("--src="):
            return argv.pop(i).split("=", 1)[1]
        i += 1
    return os.environ.get(env_var)


def _load_dvc():
    src = _src_option(sys.argv, "DVC_SRC")
    if src:
        sys.path.insert(0, os.path.abspath(src))
    try:
        from dvc.fs import localfs
        from dvc.ignore import DvcIgnoreFilter
    except ImportError as exc:
        sys.stderr.write(
            "cannot import dvc (%s). `pip install dvc`, or point DVC_SRC at a directory\n"
            "containing the dvc package.\n" % exc)
        raise SystemExit(2)
    return localfs, DvcIgnoreFilter


def _entry(argv):
    """`--entry api|walk`, popped out of argv, else $DVC_ENTRY, else api."""
    i = 0
    val = None
    while i < len(argv):
        if argv[i] == "--entry" and i + 1 < len(argv):
            del argv[i]
            val = argv.pop(i)
            break
        if argv[i].startswith("--entry="):
            val = argv.pop(i).split("=", 1)[1]
            break
        i += 1
    val = (val or os.environ.get("DVC_ENTRY") or "api").lower()
    if val not in ("api", "walk"):
        sys.stderr.write("--entry must be 'api' or 'walk', got %r\n" % val)
        raise SystemExit(2)
    return val


ENTRY = _entry(sys.argv)
LOCALFS, FILTER = _load_dvc()


def text_of(lines, what):
    """The wire says list-of-lines; anything else is a protocol violation, said out loud.

    No isinstance hedge on purpose -- accepting both shapes is what let the bench send strings
    for fifteen sessions with every test green.
    """
    if not isinstance(lines, list):
        raise TypeError("%s must be a list of lines per PROTOCOL.md, got %s"
                        % (what, type(lines).__name__))
    return "\n".join(lines)


def build_tree(root, rules, queries):
    """Write the `.dvcignore` files and the queried paths. Returns what could not be created."""
    undecidable = set()

    for directory, lines in sorted(rules.items()):
        target = os.path.join(root, directory) if directory else root
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, DVCIGNORE), "w", encoding="utf-8") as fh:
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


def _dvcs_own(query):
    return query.strip("/").split("/")[0] in DVC_OWN


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        rules, exclude = {"": request["patterns"]}, None

    if exclude:
        return [None] * len(queries)

    out = verdicts(rules, queries, request)

    # The rename artefact, detected rather than guessed: see DECLINED in the docstring. A root
    # rule file is always read (dvc loads it before it can ignore anything), so only a nested one
    # can go missing. Re-run with `!.dvcignore` appended at the root: if every verdict is
    # unchanged, readability of the rule files never mattered and the answer stands. If anything
    # moved, the rename decided it and the case is declined. The probe tree is only ever a
    # detector -- the answer reported is always the one from the corpus's rules verbatim.
    if any(d for d in rules) and out != [None] * len(queries):
        probe = dict(rules)
        probe[""] = list(probe.get("", [])) + ["!" + DVCIGNORE]
        if verdicts(probe, queries, request) != out:
            return [None] * len(queries)
    return out


def ask_walk(ignore_filter, root, queries, undecidable, request):
    """`DvcIgnoreFilter.walk`: prunable == the walk never reaches the directory.

    This is the question the `between` corpus asks, put to the code that actually prunes. A file
    query is declined: "the walk did not list it" conflates ignored-file with pruned-parent, and
    two causes behind one bit is not an answer.
    """
    seen = set()
    try:
        for r, _dirs, _files in ignore_filter.walk(LOCALFS, root):
            rel = os.path.relpath(r, root).replace(os.sep, "/")
            seen.add("" if rel == "." else rel)
    except Exception as exc:
        sys.stderr.write("dvc raised walking request %s: %s: %s\n"
                         % (request.get("id"), type(exc).__name__, exc))
        return [None] * len(queries)

    out = []
    for query in queries:
        rel = query.rstrip("/")
        if query in undecidable or _dvcs_own(query) or not query.endswith("/"):
            out.append(None)
            continue
        if not os.path.isdir(os.path.join(root, rel)):
            out.append(None)
            continue
        out.append(rel not in seen)
    return out


def verdicts(rules, queries, request):
    root = tempfile.mkdtemp(prefix="gic-dvc-")
    try:
        undecidable = build_tree(root, rules, queries)
        try:
            ignore_filter = FILTER(LOCALFS, root)
        except Exception as exc:
            # A crash is not an answer. Declining the whole case says so; folding it into
            # "not ignored" would launder a hard failure into a conformance score.
            sys.stderr.write("dvc raised building the filter for request %s: %s: %s\n"
                             % (request.get("id"), type(exc).__name__, exc))
            return [None] * len(queries)

        if ENTRY == "walk":
            return ask_walk(ignore_filter, root, queries, undecidable, request)

        out = []
        for query in queries:
            if query in undecidable or _dvcs_own(query):
                out.append(None)
                continue
            full = os.path.join(root, query.rstrip("/"))
            try:
                if query.endswith("/"):
                    out.append(bool(ignore_filter.is_ignored_dir(full)))
                else:
                    out.append(bool(ignore_filter.is_ignored_file(full)))
            except Exception as exc:
                sys.stderr.write("dvc raised on %s (request %s): %s: %s\n"
                                 % (query, request.get("id"), type(exc).__name__, exc))
                out.append(None)
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
