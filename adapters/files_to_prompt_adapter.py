#!/usr/bin/env python3
"""Level-2 adapter for simonw/files-to-prompt.

files-to-prompt is not a library you can ask "is this path ignored?" -- it is a walker that
prints the files it decides to include. So this adapter asks the question the only way the
subject actually answers it: materialise the rule files and the queried paths in a temporary
tree, run `process_path` over the root, and read the verdict off the output. A path that comes
out of the walk was not ignored; a path that never appears was.

That is the whole point of level 2. The decision being measured here does not live in a
pattern-matching library at all -- it lives in `cli.py`'s `os.walk` loop, in the six lines that
`extend` one flat list of rules as they descend. Nobody's test suite owns those lines.

Directory queries (a trailing `/`) get `null`: a walker only ever reports files, and inferring
"this directory was pruned" from the absence of its children would be me answering for the
tool. The level-2 corpus has no directory queries yet anyway.

Where the code comes from, in order:
  1. `--src <path>` on this adapter's own command line, or `$FILES_TO_PROMPT_SRC`: a checkout
     or `cli.py` itself, for when you are measuring a branch, a PR, or a machine without pip.
  2. `import files_to_prompt.cli` -- the installed package, which is what you want.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile


def _src_option(argv, env_var):
    """`--src <path>` (or `--src=<path>`) off the adapter's command line, else $<env_var>.

    gic.py runs the adapter as a command, so the flag survives harnesses that will not let you
    set an environment variable inline. Popped out of argv so nothing downstream trips over it.
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


def _load_cli():
    src = _src_option(sys.argv, "FILES_TO_PROMPT_SRC")
    if not src:
        try:
            import files_to_prompt.cli as cli
            return cli
        except ImportError:
            sys.stderr.write(
                "files_to_prompt is not installed. `pip install files-to-prompt`, or point\n"
                "FILES_TO_PROMPT_SRC at a checkout (or at its files_to_prompt/cli.py).\n")
            raise SystemExit(2)
    path = src if src.endswith(".py") else os.path.join(src, "files_to_prompt", "cli.py")
    if not os.path.isfile(path):
        sys.stderr.write("FILES_TO_PROMPT_SRC does not point at a cli.py: %s\n" % path)
        raise SystemExit(2)
    _stub_click_if_missing()
    spec = importlib.util.spec_from_file_location("_f2p_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_click_if_missing():
    """cli.py needs click for its argument parsing and for two coloured stderr warnings.

    This adapter never goes through the command line -- it calls `process_path` directly --
    so when click is absent, no-op decorators are enough to get the module imported, and the
    walk being measured stays byte-identical. Install click if you would rather not trust me
    on that; the real package wins when it is there.
    """
    try:
        import click  # noqa: F401
        return
    except ImportError:
        pass
    import types

    def decorator_factory(*args, **kwargs):
        def decorate(func=None):
            return func
        return decorate

    stub = types.ModuleType("click")
    stub.echo = lambda message="", err=False, **kw: sys.stderr.write(str(message) + "\n")
    stub.style = lambda text, **kw: text
    stub.__getattr__ = lambda name: decorator_factory
    sys.modules["click"] = stub


CLI = _load_cli()


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
        if query.endswith("/"):
            continue
        full = os.path.join(root, query)
        try:
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
    """Run the subject over the tree and collect the repo-relative paths it printed."""
    printed = []
    CLI.process_path(
        root,
        extensions=(),
        include_hidden=True,      # otherwise "hidden" would masquerade as "ignored"
        ignore_files_only=False,
        ignore_gitignore=False,
        gitignore_rules=[],
        ignore_patterns=(),
        writer=printed.append,
        claude_xml=False,
        markdown=False,
    )
    seen = set()
    prefix = root.rstrip("/") + "/"
    for chunk in printed:
        for line in str(chunk).splitlines():
            if line.startswith(prefix):
                seen.add(line[len(prefix):])
    return seen


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2:
        rules, exclude = request.get("rules") or {}, request.get("exclude")
    else:
        # Level 1 is a level-2 tree with one rule file at the root. Answering it costs nothing
        # and the numbers are comparable with the libraries'.
        rules, exclude = {"": request["patterns"]}, None

    root = tempfile.mkdtemp(prefix="gic-f2p-")
    try:
        undecidable = build_tree(root, rules, exclude, queries)
        included = walk(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    out = []
    for query in queries:
        if query.endswith("/") or query in undecidable:
            out.append(None)
        else:
            out.append(query not in included)
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
