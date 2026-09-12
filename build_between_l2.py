#!/usr/bin/env python3
"""The fourth level-2 variant: the directories a path is reached *through*.

The three frozen variants ask about `D/name` as a file, `D/name` as a directory, and a file
underneath `D/name`. None of them asks about `D` on its own, or about the `_gic_deep` between `D`
and the leaf — the directories git walks through on its way down. That is a hole with a shape: a
matcher that scans patterns against the full path only can be wrong about every intermediate
directory and still answer all 8,953 frozen questions correctly, because nobody asks.

It is also the exact shape of a fix. `git-pkgs/gitignore`
[#27](https://github.com/git-pkgs/gitignore/pull/27) makes `Match` check each parent directory
before the full path, which is the ancestor walk git does by construction. A bench that cannot
ask about an ancestor cannot say much about a patch that adds one.

The oracle is not the triple rule the file queries use. For a directory those three answers are
one answer wearing three hats: `check-ignore d/` matches the pattern `d/*` because `*` happily
matches the empty string, `!! d/` in `status --ignored` conflates "this directory is ignored" with
"everything in it happens to be", and "nothing staged below" is true of both. They fire together,
so agreement buys nothing. Level 1 already solved this with the re-inclusion probe, and this uses
the same three rules:

    A. `check-ignore -v` on the bare path       -> the deciding pattern
    B. the re-inclusion probe                   -> does git actually descend in there?
    C. nothing staged anywhere below            -> a one-directional falsifier

A and B must agree; C may not contradict them. Anything else is dropped into
`corpus/excluded_l2_between.json` and counted, rather than guessed at.

One difference from level 1's probe. There the canary's negation goes in the root `.gitignore`,
because there is only one rule file. Here there is a tree, and in git a deeper rule file beats a
shallower one outright — so a `!d/CANARY` at the root can be overruled by a `*` in `d/.gitignore`,
and the probe would report an exclusion that isn't one. The negation therefore goes in the probed
directory's *own* `.gitignore`, created if absent: the deepest file entitled to speak about its
contents. If the directory really is excluded, git never descends, never reads that file, and the
canary is not staged — which is the signal being read.

The corpus this writes carries the **original** rule files. The canary lines are how the truth was
obtained, not part of the tree anyone is asked about.

It ships as its own file, for the same reason `cases_l2_exclude.json` does: its denominator is not
`cases_l2.json`'s, and folding it in would silently rewrite every number in the README.

    python3 build_between_l2.py            # build corpus/cases_l2_between.json
    python3 gic.py --level 2 --corpus corpus/cases_l2_between.json -- <your adapter>
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_corpus import CANARY, check_ignore, git  # noqa: E402

FROZEN = os.path.join(HERE, "corpus", "cases_l2.json")
OUT = os.path.join(HERE, "corpus", "cases_l2_between.json")
OUT_BAD = os.path.join(HERE, "corpus", "excluded_l2_between.json")


def ancestors(path):
    """Every proper directory on the way to `path`, root excluded."""
    parts = path.strip("/").split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def staged_in(repo):
    res = git(["add", "-A", "-n"], repo)
    out = set()
    for line in res.stdout.split("\n"):
        line = line.strip()
        if line.startswith("add '") and line.endswith("'"):
            out.add(line[5:-1])
    return out


def materialise(dest, rules, file_paths):
    """Write the rule tree and every query path as a file, and check nothing overwrote a rule."""
    os.makedirs(dest, exist_ok=True)
    git(["init", "-q"], dest)
    git(["config", "user.email", "corpus@example.invalid"], dest)
    git(["config", "user.name", "corpus"], dest)
    for d, text in rules.items():
        full = os.path.join(dest, d, ".gitignore") if d else os.path.join(dest, ".gitignore")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    made = []
    for q in file_paths:
        full = os.path.join(dest, q)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if os.path.isdir(full):
                continue
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        except (OSError, ValueError):
            continue
        made.append(q)
    # The same bolt the rest of the bench has: if materialising a path rewrote a rule file, every
    # verdict in this repository is about a tree other than the one being described.
    for d, text in rules.items():
        full = os.path.join(dest, d, ".gitignore") if d else os.path.join(dest, ".gitignore")
        with open(full, encoding="utf-8") as fh:
            if fh.read() != text:
                raise RuntimeError("rule file %r changed while materialising paths"
                                   % (d or "<root>"))
    return made


def reinclusion_probe(base, rules, file_paths, dirs):
    """{dir: True if a canary inside it can be re-included} — i.e. if git descends into it."""
    repo = os.path.join(base, "probe")
    materialise(repo, rules, file_paths)
    probed = []
    for d in dirs:
        full = os.path.join(repo, d)
        if not os.path.isdir(full):
            continue
        try:
            with open(os.path.join(full, CANARY), "w", encoding="utf-8") as fh:
                fh.write("x\n")
            rule_file = os.path.join(full, ".gitignore")
            existing = ""
            if os.path.exists(rule_file):
                with open(rule_file, encoding="utf-8") as fh:
                    existing = fh.read()
                if existing and not existing.endswith("\n"):
                    existing += "\n"
            with open(rule_file, "w", encoding="utf-8") as fh:
                fh.write(existing + "!" + CANARY + "\n")
        except (OSError, ValueError):
            continue
        probed.append(d)
    staged = staged_in(repo)
    return {d: ("%s/%s" % (d, CANARY)) in staged for d in probed}


def build_case(case, base):
    rules, paths = case["rules"], case["queries"]
    repo = os.path.join(base, "r")
    materialise(repo, rules, paths)

    cand = sorted({a for q in paths for a in ancestors(q)})
    cand = [d for d in cand
            if d.split("/")[0] != ".git" and os.path.isdir(os.path.join(repo, d))]
    if not cand:
        return None, []

    verdict = check_ignore(repo, cand)                              # A
    descends = reinclusion_probe(base, rules, paths, cand)          # B
    staged = staged_in(repo)                                        # C

    def nothing_below(d):
        return not any(s.startswith(d + "/") for s in staged)

    queries, ignored, meta, bad = [], [], [], []
    for d in cand:
        v = verdict.get(d)
        if v is None or d not in descends:
            continue
        _, a, pattern = v
        b = not descends[d]        # not re-includable == git does not descend == excluded
        c = nothing_below(d)
        if a != b or (b and not c):
            bad.append({"repo": case["repo"], "path": d, "check_ignore": a,
                        "reinclusion": b, "nothing_staged": c, "pattern": pattern})
            continue
        queries.append(d + "/")
        ignored.append(a)
        meta.append({"class": "between", "kind": "dir", "depth": d.count("/") + 1,
                     "owner": "", "from_pattern": None, "git_pattern": pattern})
    if not queries:
        return None, bad
    return ({"repo": case["repo"], "branch": case.get("branch"), "level": 2,
             "variant": "between", "rules": rules, "queries": queries, "ignored": ignored,
             "meta": meta}, bad)


def build():
    with open(FROZEN, encoding="utf-8") as fh:
        doc = json.load(fh)
    sources = [c for c in doc["cases"] if c.get("variant", "files") == "files"]
    print("%d repositories with a `files` case in the frozen corpus" % len(sources))

    cases, dropped = [], []
    for c in sources:
        base = tempfile.mkdtemp(prefix="gic_between_")
        try:
            case, bad = build_case(c, base)
        finally:
            shutil.rmtree(base, ignore_errors=True)
        dropped.extend(bad)
        if case is None:
            print("  %-32s no questions (%d dropped)" % (c["repo"], len(bad)))
            continue
        cases.append(case)
        print("  %-32s %4d queries, %3d ignored, %3d dropped"
              % (c["repo"], len(case["queries"]), sum(case["ignored"]), len(bad)))
        sys.stdout.flush()

    out = {"level": 2, "oracle": doc["oracle"], "n_repos": len(cases), "n_cases": len(cases),
           "n_queries": sum(len(c["queries"]) for c in cases), "n_excluded": len(dropped),
           "skipped_repos": [], "cases": cases,
           "note": "variant `between`: the intermediate directories of every path in "
                   "cases_l2.json, with the re-inclusion probe as the oracle for each one"}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    with open(OUT_BAD, "w", encoding="utf-8") as fh:
        json.dump({"n": len(dropped), "rows": dropped[:400]}, fh)
    print("\n%d repositories, %d queries, %d ignored, %d dropped on oracle disagreement"
          % (len(cases), out["n_queries"], sum(sum(c["ignored"]) for c in cases), len(dropped)))
    return out


if __name__ == "__main__":
    build()
