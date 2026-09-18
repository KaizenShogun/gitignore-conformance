#!/usr/bin/env python3
"""Adapter for the *matcher* of BurntSushi's `ignore` crate -- not for a walker built on it.

`ripgrep_adapter.py` measures the binary, and the binary answers 0 divergences. That number is
about ripgrep's **walk**: it descends level by level and prunes excluded directories, so its
matcher is never asked about a path underneath one. The crate also exports the matcher on its own,
and its doc-comment offers `matched_path_or_any_parents` precisely for the case where nobody walks:
"use this when you have a list of paths without hierarchy". Consumers that hold a list of paths --
from `git ls-files`, from a manifest, from a database -- take that door. This adapter measures
that door.

Two entry points, same harness, so the contrast is paired:

  * `--api parents` (default): `Gitignore::matched_path_or_any_parents`
  * `--api matched`: `Gitignore::matched`

The question is asked the way the crate asks for it: path **without** a trailing slash, with
`is_dir` as a separate flag, taken from the corpus's own `dir/` notation. Feeding it `__tmp/`
instead of (`__tmp`, is_dir=true) changes the answer inside the same library, and the corpus, not
me, decides which paths are directories.

Level 2 is DECLINED outright (`null` for everything): a flat matcher has no tree layer, and
inventing one here would put my eight lines under test instead of the crate's.

Needs the Rust side built once, and it ships with the adapter:

    cargo build --release --manifest-path adapters/ig_probe/Cargo.toml   # ~40 s
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The Rust side ships next door, in `adapters/ig_probe/`. Build it once with
# `cargo build --release --manifest-path adapters/ig_probe/Cargo.toml` (~40 s).
DEFAULT_BIN = os.path.join(HERE, "ig_probe", "target", "release", "gic_serve")


def _option(argv, flag, default):
    i = 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            del argv[i]
            return argv.pop(i)
        if argv[i].startswith(flag + "="):
            return argv.pop(i).split("=", 1)[1]
        i += 1
    return default


API = _option(sys.argv, "--api", "parents")
BIN = _option(sys.argv, "--src", None) or DEFAULT_BIN
if not os.path.exists(BIN):
    sys.stderr.write("no gic_serve binary at %s -- run `_scratch/_m176_build.py`.\n" % BIN)
    raise SystemExit(2)

SERVER = subprocess.Popen([BIN] + (["--api=matched"] if API == "matched" else []),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, bufsize=1)


def text_lines(patterns, what):
    """The wire says list-of-lines. No isinstance hedge -- a string here would be read as 40
    one-character rules and answered with a straight face (see the dvc adapter)."""
    if not isinstance(patterns, list):
        raise TypeError("%s must be a list of lines per PROTOCOL.md, got %s"
                        % (what, type(patterns).__name__))
    return patterns


def answer(request):
    queries = request["queries"]
    if request.get("level", 1) == 2 or request.get("exclude"):
        return [None] * len(queries)

    frame = ["CASE"]
    for rule in text_lines(request["patterns"], "patterns"):
        # A rule with a newline in it would desync the frame; the corpus has none, and if one
        # ever appears I want it loud rather than silently mis-scored.
        if "\n" in rule or "\r" in rule:
            raise ValueError("rule with an embedded newline: %r" % rule)
        frame.append("R" + rule)
    for query in queries:
        frame.append("Q" + query)
    frame.append("GO")
    SERVER.stdin.write("\n".join(frame) + "\n")
    SERVER.stdin.flush()

    out = []
    while True:
        line = SERVER.stdout.readline()
        if not line:
            raise RuntimeError("gic_serve died on request %s" % request.get("id"))
        line = line.rstrip("\n")
        if line == ".":
            break
        if line == "E":  # the crate refused the rule file; declining beats guessing
            return [None] * len(queries)
        out.append(line == "1")
    if len(out) != len(queries):
        raise RuntimeError("gic_serve answered %d of %d queries" % (len(out), len(queries)))
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
