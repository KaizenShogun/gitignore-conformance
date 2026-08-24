#!/usr/bin/env python3
"""Adapter for `gitignore_parser` (https://github.com/mherrmann/gitignore_parser).

    pip install gitignore_parser

Its matcher takes an absolute path, so the queries are hung off a base directory that never
exists on disk -- the library does the joining and normalising with `os.path.abspath`, and never
touches the filesystem. The trailing slash survives that trip, which is what makes directory
queries answerable here.
"""
import json
import sys

from gitignore_parser import parse_gitignore_str

BASE = "/gic"


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        matches = parse_gitignore_str("\n".join(req["patterns"]), base_dir=BASE)
        answers = []
        for query in req["queries"]:
            try:
                answers.append(bool(matches("%s/%s" % (BASE, query))))
            except Exception:
                answers.append(None)      # an exception is a gap, not a verdict
        sys.stdout.write(json.dumps({"id": req["id"], "ignored": answers}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
