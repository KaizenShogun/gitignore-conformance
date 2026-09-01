#!/usr/bin/env python3
"""Level-2 adapter for psf/black.

black is here as the *control* for level 2. files-to-prompt fails 13.1% of level-1 cases
before the tree question is even asked, so its 26.8% sibling residue could always be read as
"that tool is bad" rather than "this layer is hard". black matches with `pathspec` (69
divergences in 9,852 level-1 cases, 0.7%) and -- this is the part that matters -- it *promises*
the level-2 layer itself: `gen_python_files` takes `gitignore_dict: dict[Path, GitIgnoreSpec]`
and rebuilds it on every descent, one entry per directory. That is the axis under test, written
by the subject, not by me. Measuring anything I had to add myself would be measuring me.

Like files-to-prompt, black is a walker, not a "is this path ignored?" function, so the question
gets asked the only way it answers: materialise the rule files and the queried paths in a
temporary tree, run the walk, and read the verdict off what comes out. A path the walk yields
was not ignored.

`gen_python_files` is internal API. It is also exactly the layer being measured -- going in
through `main()` would drag in config discovery, caching and a formatter, none of which have an
opinion about nested `.gitignore` files.

DEVIATIONS FROM BLACK'S DEFAULTS, all of them on the file-selection side, none on the
`.gitignore` path:
  * `include=re.compile(".")` instead of `\\.pyi?$`. The corpus queries leaves of every name
    (`.DS_Store`, `npm-debug.log`, `dist`); with the stock filter every non-Python answer would
    come back "ignored" and the measurement would be of black's suffix regex. `include` is
    consulted *after* the gitignore check and only for files, so widening it cannot change a
    single gitignore decision.
  * `exclude=re.compile("(?!)")` -- a regex that matches nothing, standing in for black's
    default exclude list (`.venv`, `build`, `dist`, ...). Those names collide head-on with the
    corpus, and they are a different feature.
  * `extend_exclude=force_exclude=None`, `verbose=False`, `quiet=True`.
  * `.ipynb` leaves are declined (`null`) unless the Jupyter extras are installed: without them
    black skips notebooks in a branch below the gitignore check, which would masquerade as
    "ignored". The frozen level-2 corpus contains no such query; this is for the next one.

Where the code comes from, in order:
  1. `--src <dir>` -- a directory to put on `sys.path`, given on this adapter's own command
     line. gic.py runs the adapter as a command, so this survives every harness that will not
     let you set an environment variable inline.
  2. `$BLACK_SRC` -- the same thing through the environment.
  3. `import black` -- the installed package.
Options 1 and 2 exist for a checkout, or (as here) for a machine with no pip, where the wheels
were unzipped by hand.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


def _src_option(argv, env_var):
    """`--src <dir>` (or `--src=<dir>`) off the adapter's command line, else $<env_var>.

    Popped out of argv, not just read, so nothing downstream trips over it.
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


def _load_black():
    src = _src_option(sys.argv, "BLACK_SRC")
    if src:
        sys.path.insert(0, src)
    try:
        from black.files import gen_python_files, get_gitignore
        from black.handle_ipynb_magics import jupyter_dependencies_are_installed
        from black.report import Report
    except ImportError as exc:
        sys.stderr.write(
            "cannot import black (%s). `pip install black`, or point BLACK_SRC at a\n"
            "directory containing the black package.\n" % exc)
        raise SystemExit(2)
    return gen_python_files, get_gitignore, Report, jupyter_dependencies_are_installed


GEN, GET_GITIGNORE, REPORT, JUPYTER_OK = _load_black()

INCLUDE_ALL = re.compile(".")
EXCLUDE_NOTHING = re.compile("(?!)")
NOTEBOOKS_MEASURABLE = JUPYTER_OK(warn=False)


class CapturingReport(REPORT):
    """black's own report channel, kept instead of thrown away.

    `path_ignored` is where black states, in its own words, that a path lost to a `.gitignore`.
    Directories go through it too -- they are pruned before recursion -- which is the one thing
    a print-the-files walker like files-to-prompt can never tell you, and the reason directory
    queries are answerable here.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gitignored = set()

    def path_ignored(self, path, message):
        if "gitignore" in message:
            self.gitignored.add(Path(path))


def text_of(lines, what):
    """The wire says list-of-lines. Anything else is a protocol violation, so say so loudly.

    This used to read `"\\n".join(lines) if isinstance(lines, list) else lines`, and that hedge
    is exactly how the level-2 wire spent fifteen sessions sending strings where PROTOCOL.md
    promised lists without a single test going red. Accepting both shapes is not robustness: it
    switches off the only detector there was.
    """
    if not isinstance(lines, list):
        raise TypeError("%s must be a list of lines per PROTOCOL.md, got %s"
                        % (what, type(lines).__name__))
    return "\n".join(lines)


def build_tree(root, rules, exclude, queries):
    """Write the rule files and the queried paths. Returns the paths we could not create."""
    undecidable = set()

    for directory, lines in sorted(rules.items()):
        target = os.path.join(root, directory) if directory else root
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write(text_of(lines, "rules[%r]" % directory))

    if exclude:
        info = os.path.join(root, ".git", "info")
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "exclude"), "w", encoding="utf-8") as fh:
            fh.write(text_of(exclude, "exclude"))

    # Longest first: if one query is a parent directory of another, the deeper one wins and
    # the shallower becomes a directory we cannot represent as a file.
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


def walk(root):
    """Run the subject over the tree. Returns (files it yielded, directories it pruned)."""
    root = Path(root).resolve()
    report = CapturingReport(check=True, quiet=True, verbose=False)
    yielded = set()
    for path in GEN(
        root.iterdir(),
        root,
        INCLUDE_ALL,
        EXCLUDE_NOTHING,
        None,          # extend_exclude
        None,          # force_exclude
        report,
        {root: GET_GITIGNORE(root)},
        verbose=False,
        quiet=True,
    ):
        yielded.add(Path(path).relative_to(root).as_posix())
    pruned = {p.relative_to(root).as_posix() for p in report.gitignored}
    return yielded, pruned


def _under_pruned(query, pruned):
    """A directory black never reached because an ancestor lost to a rule is still ignored."""
    parts = query.strip("/").split("/")
    return any("/".join(parts[:i]) in pruned for i in range(1, len(parts) + 1))


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        # Level 1 is a level-2 tree with one rule file at the root. Answering it costs nothing
        # and makes black's tree layer comparable with its own matching layer.
        rules, exclude = {"": request["patterns"]}, None

    root = tempfile.mkdtemp(prefix="gic-black-")
    try:
        undecidable = build_tree(root, rules, exclude, queries)
        try:
            yielded, pruned = walk(root)
        except Exception as exc:
            # black raises out of `get_gitignore` on a pattern its `pathspec` backend rejects
            # -- and some of those are patterns git accepts, so the corpus contains them. A
            # crash is not an answer, so the whole case is declined and said out loud on
            # stderr. Swallowing it into "not ignored" would turn a hard failure into a
            # conformance number, which is the exact dishonesty this tool exists to avoid.
            sys.stderr.write("black raised on request %s: %s: %s\n"
                             % (request.get("id"), type(exc).__name__, exc))
            return [None] * len(queries)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    out = []
    for query in queries:
        if query in undecidable:
            out.append(None)
        elif query.endswith("/"):
            out.append(_under_pruned(query, pruned))
        elif query.endswith(".ipynb") and not NOTEBOOKS_MEASURABLE:
            out.append(None)
        else:
            out.append(query not in yielded)
    return out


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
