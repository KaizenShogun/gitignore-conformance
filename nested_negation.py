#!/usr/bin/env python3
"""Ten hand-built trees for the one question the real-world corpus barely asks: what happens when
a deeper `.gitignore` negates something an ancestor excluded.

    python3 nested_negation.py -- python3 adapters/black_adapter.py

The level-2 corpus is 33 real repositories, and real repositories rarely put a negation in a
nested rule file *and* a second, unrelated pattern in the ancestor that should still win. So a
fix for "nested negations never take effect" can score perfectly there and still break the case
where the negation re-includes a directory and an ancestor glob (`*_pb2.py`) must keep excluding
a file inside it. These trees ask that on purpose.

Oracle: each tree is a real repository, and `git ls-files --others --exclude-standard` lists
exactly the untracked files git does not ignore. No `check-ignore`: it does not model an excluded
parent directory, and with `-v` its exit status lies (see PROTOCOL.md). Only file queries.

The subject is any level-2 adapter (PROTOCOL.md). Exit status: 0 if it agrees on every query.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# name -> ({rule-file directory: contents}, [queried files])
TREES = {
    "F1 the #5376 shape: a deeper !dir re-includes": (
        {"": "tmpbuild/", "packages": "!playground/tmpbuild/"},
        ["packages/playground/tmpbuild/settings.py", "packages/other/tmpbuild/x.py",
         "packages/playground/keep.py"]),
    "F2 a re-included dir must not shadow an ancestor glob": (
        {"": "build_cache\n*_pb2.py", "pkg": "!playground/build_cache"},
        ["pkg/playground/build_cache/msg_pb2.py", "pkg/playground/build_cache/ok.py"]),
    "F3 F2 with trailing slashes": (
        {"": "build_cache/\n*_pb2.py", "pkg": "!playground/build_cache/"},
        ["pkg/playground/build_cache/msg_pb2.py", "pkg/playground/build_cache/ok.py"]),
    "F4 an excluded parent cannot be re-entered by a file negation": (
        {"": "build/", "sub": "!build/keep.py"},
        ["sub/build/keep.py", "sub/build/other.py"]),
    "F5 a deeper file re-ignores what the root re-included": (
        {"": "*.py\n!keep.py", "sub": "keep.py"},
        ["sub/keep.py", "keep.py", "sub/x.py"]),
    "F6 a deeper !file beats a root glob": (
        {"": "*.gen.py", "a": "!x.gen.py"},
        ["a/x.gen.py", "a/y.gen.py", "x.gen.py"]),
    "F7 an anchored root dir, a deeper !dir": (
        {"": "/a/b/", "a": "!b/"},
        ["a/b/f.py", "a/c.py"]),
    "F8 a deeper !* re-includes everything below it": (
        {"": "secret*", "a": "!*"},
        ["a/secret.py", "secret.py", "b/secret.py"]),
    "F9 three levels: the middle excludes, the deepest re-includes": (
        {"a": "*.tmp.py", "a/b": "!keep.tmp.py"},
        ["a/b/keep.tmp.py", "a/b/drop.tmp.py", "a/drop.tmp.py"]),
    "F10 a root negation does not beat a deeper exclusion": (
        {"": "!cache/", "a": "cache/"},
        ["a/cache/x.py", "cache/y.py"]),
}


def git_truth(rules, files):
    root = tempfile.mkdtemp(prefix="gic-nn-")
    try:
        subprocess.run(["git", "init", "-q", root], check=True)
        for d, text in rules.items():
            p = Path(root, d)
            p.mkdir(parents=True, exist_ok=True)
            (p / ".gitignore").write_text(text + "\n")
        for f in files:
            p = Path(root, f)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        kept = subprocess.run(["git", "-C", root, "ls-files", "--others", "--exclude-standard"],
                              capture_output=True, text=True, check=True).stdout.split()
        return {f: f not in kept for f in files}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        sys.exit("usage: nested_negation.py -- <level-2 adapter command>")
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    agree = total = declined = 0
    for i, (name, (rules, files)) in enumerate(TREES.items()):
        truth = git_truth(rules, files)
        proc.stdin.write(json.dumps({"id": i, "level": 2, "queries": files,
                                     "rules": {d: t.split("\n") for d, t in rules.items()}})
                         + "\n")
        proc.stdin.flush()
        got = json.loads(proc.stdout.readline())["ignored"]
        print("\n" + name)
        for d, t in rules.items():
            print("    %-18s %s" % ((d + "/" if d else "") + ".gitignore", t.replace("\n", " ; ")))
        for f, g in zip(files, got):
            if g is None:
                declined += 1
                mark = "declined"
            else:
                total += 1
                agree += g == truth[f]
                mark = "" if g == truth[f] else "  <-- WRONG"
            say = lambda b: "ignored" if b else "kept"
            print("  %-42s git %-7s  adapter %-8s%s"
                  % (f, say(truth[f]), "-" if g is None else say(g), mark))
    proc.stdin.close()
    proc.wait()
    print("\n%d of %d answered queries agree with git%s"
          % (agree, total, " (%d declined)" % declined if declined else ""))
    sys.exit(0 if agree == total else 1)


if __name__ == "__main__":
    main()
