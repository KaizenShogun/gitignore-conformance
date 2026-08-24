#!/usr/bin/env python3
"""Adapter for `pathspec` (https://github.com/cpburnz/python-pathspec).  pip install pathspec

`GitIgnoreSpec` is the class that claims to follow git; `PathSpec` with `GitWildMatchPattern` is
the older entry point and gets the layering wrong on purpose, so this measures the former.
"""
import json
import sys

import pathspec


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        spec = pathspec.GitIgnoreSpec.from_lines(req["patterns"])
        answers = [bool(spec.match_file(q)) for q in req["queries"]]
        sys.stdout.write(json.dumps({"id": req["id"], "ignored": answers}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
