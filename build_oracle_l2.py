#!/usr/bin/env python3
"""Level-2 oracle: materialise the *tree* of rule files and ask git what it ignores.

`build_corpus_l2.py` did the network half -- 453 real `.gitignore` files from 42 large repos,
each with the directory it lives in. This does the half that needs no network at all: it writes
those files back out at their own depths, drops candidate paths around them, and asks git.

The question level 2 exists to ask, and level 1 cannot:

    does a rule stop at the edge of its own subtree?

`src/.gitignore` may say `*.log`. That decides `src/a.log`. It must NOT decide `docs/a.log` and it
must NOT decide `a.log` at the root. A library that takes one list of patterns has no way to get
this wrong, because it is never told there is more than one list -- which is exactly why the bug
lives one level up, in the eight lines of the caller. `files-to-prompt` accumulates rule files with
`gitignore_rules.extend(read_gitignore(root))` inside its walk (`cli.py:122-128`), so its list only
ever grows: a rule read in `a/` is still live in the sibling `b/`. That is the seam this corpus is
built to point at, and pointing at it needs git's verdict, not my reading of gitignore(5).

Query classes, all four generated per nested rule file, so the corpus cannot become "everything is
ignored" by construction:

  own      -- `D/name`, inside the subtree that owns the rule           (should apply)
  sibling  -- `S/name`, in a different subtree at the same depth        (must NOT apply)
  root     -- `name`, above the rule file entirely                      (must NOT apply)
  deeper   -- `D/<sub>/name`, further down inside the same subtree      (should apply)

plus root patterns dropped *inside* nested dirs, which is the other direction: rules from above
reach down, unless rule 3 (a parent directory excluded outright) stops git from ever walking in.

Honesty, same as level 1 and stated in the same words: the **rule files are real** -- verbatim,
from named repos, sha-addressable in the cache. The **paths are derived** from those repos' own
patterns via `concretize()`, which refuses to invent character classes or backslashes. Sibling
directory names are the other real rule directories where the tree has them, and a synthetic
`_gic_sib` only when the repo has no real sibling to use.

Only file paths are emitted. Directory questions at level 2 need the re-inclusion probe that level
1 uses, per rule file rather than per repo, and that is a second pass -- said here rather than
discovered later by someone counting.

    python3 build_oracle_l2.py            # build corpus/cases_l2.json
    python3 build_oracle_l2.py report     # read what is on disk, no work
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_corpus import check_ignore, concretize, git, split_patterns  # noqa: E402

CACHE = os.path.join(HERE, "corpus", "_cache_l2")
OUT = os.path.join(HERE, "corpus", "cases_l2.json")
OUT_BAD = os.path.join(HERE, "corpus", "excluded_l2.json")

MAX_NESTED_DIRS = 6      # rule files per repo, beyond the root
MAX_PATTERNS = 6         # patterns taken from each rule file
SIB = "_gic_sib"         # synthetic sibling, used only when the repo offers no real one
DEEP = "_gic_deep"       # synthetic subdirectory, for the "deeper still" class
KEEP = "_gic_keep"       # the one file inside a directory query, so git has something to name


def load_index():
    with open(os.path.join(CACHE, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_rule_file(repo, directory):
    path = os.path.join(directory, ".gitignore") if directory else ".gitignore"
    flat = repo.replace("/", "__") + "@@" + path.replace("/", "__")
    key = os.path.join(CACHE, flat)
    if not os.path.exists(key):
        return None
    with open(key, encoding="utf-8") as fh:
        return fh.read()


def usable_dir(d):
    """A rule directory we can materialise and reason about."""
    if not d:
        return True
    parts = d.split("/")
    return all(p not in ("", ".", "..", ".git") for p in parts) and len(d) < 160


def pick_dirs(entry):
    """Root plus a spread of nested rule directories: shallowest first, then deepest.

    Taking the first N in path order would hand every slot to whatever sorts early -- in `next.js`
    that is 352 files under one prefix. Interleaving shallow and deep keeps both the ordinary case
    and `pytorch`'s nine-levels-down one in the corpus.
    """
    nested = sorted((d for d in entry["dirs"] if d and usable_dir(d)),
                    key=lambda d: (d.count("/"), d))
    if len(nested) <= MAX_NESTED_DIRS:
        chosen = nested
    else:
        half = MAX_NESTED_DIRS // 2
        chosen = nested[:half] + nested[-(MAX_NESTED_DIRS - half):]
    return chosen


def names_from(text, limit=MAX_PATTERNS):
    """Concrete leaf names derived from a rule file's own patterns, deduped and capped."""
    dirs, files, negs = split_patterns(text)
    out, seen = [], set()
    # Interleave so a file of 200 directory patterns does not crowd out the file patterns.
    for group in zip_longest_(files, dirs, negs):
        for pattern in group:
            if pattern is None:
                continue
            body = concretize(pattern)
            if not body or body in seen:
                continue
            # An absolute-ish or multi-segment pattern still yields a usable relative path; keep
            # it whole, because `/tools/x` vs `tools/x` is precisely an anchoring question.
            seen.add(body)
            out.append((body, pattern))
            if len(out) >= limit:
                return out
    return out


def zip_longest_(*seqs):
    n = max((len(s) for s in seqs), default=0)
    for i in range(n):
        yield tuple(s[i] if i < len(s) else None for s in seqs)


def build_repo_case(repo, entry, workdir, as_dirs=False):
    """Materialise one repo's rule tree, ask git, return (case, disagreements).

    `as_dirs` builds the same queries as *directories* instead of files. That is a separate case
    for the same repo, not a bigger one: `d/foo` cannot be a file and a directory in one tree, and
    a directory-only rule (`build/`) matches exactly one of them. Directories are where a walker
    can go wrong in a way files cannot show -- git prunes an ignored directory and never looks
    inside, so a tool that gets the prune wrong is wrong about everything below it at once.
    """
    dirs = pick_dirs(entry)
    rules = {}
    root_text = load_rule_file(repo, "")
    if root_text is not None:
        rules[""] = root_text
    for d in dirs:
        text = load_rule_file(repo, d)
        if text is not None:
            rules[d] = text
    if len([k for k in rules if k]) == 0:
        return None, []          # no layering here; level 1 already covers this repo

    # ---- the queries, each labelled with why it exists
    queries = []                 # [(path, cls, owner_dir, source_pattern)]
    seen = set()

    def add(path, cls, owner, pattern):
        if not path or path in seen:
            return
        parts = path.split("/")
        if any(p in ("", ".", "..", ".git") for p in parts):
            return
        if os.path.basename(path) == ".gitignore":
            return               # never a query: it is the instrument, not the sample
        if path in rules or any(path.startswith(d + "/") and path.endswith("/.gitignore")
                                for d in rules):
            return
        seen.add(path)
        queries.append((path, cls, owner, pattern))

    nested = [d for d in rules if d]
    for d in nested:
        parent = os.path.dirname(d)
        siblings = [o for o in nested if o != d and os.path.dirname(o) == parent]
        sib = siblings[0] if siblings else (parent + "/" + SIB if parent else SIB)
        for body, pattern in names_from(rules[d]):
            add("%s/%s" % (d, body), "own", d, pattern)
            add("%s/%s" % (sib, body), "sibling", d, pattern)
            add(body, "root", d, pattern)
            add("%s/%s/%s" % (d, DEEP, body), "deeper", d, pattern)
        # rules from above, dropped inside this subtree
        for body, pattern in names_from(rules.get("", ""), limit=3):
            add("%s/%s" % (d, body), "from_root", "", pattern)

    if not queries:
        return None, []

    # ---- materialise
    repo_dir = os.path.join(workdir, "r")
    os.makedirs(repo_dir)
    git(["init", "-q"], repo_dir)
    git(["config", "user.email", "corpus@example.invalid"], repo_dir)
    git(["config", "user.name", "corpus"], repo_dir)
    for d, text in rules.items():
        full = os.path.join(repo_dir, d, ".gitignore") if d else os.path.join(repo_dir,
                                                                             ".gitignore")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    created = []
    for path, cls, owner, pattern in queries:
        full = os.path.join(repo_dir, path)
        try:
            if as_dirs:
                if os.path.isfile(full):
                    continue     # a rule file got there first; not a directory question
                os.makedirs(full, exist_ok=True)
                # git tracks no empty directories, so `add -n` -- one of the three oracles --
                # would say nothing at all about an empty one. One file inside makes it visible.
                with open(os.path.join(full, KEEP), "w", encoding="utf-8") as fh:
                    fh.write("x\n")
            else:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                if os.path.isdir(full):
                    continue     # a rule directory got there first; not a file question
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write("x\n")
        except (OSError, ValueError):
            continue             # unrepresentable on this filesystem; not a case
        created.append((path, cls, owner, pattern))
    if not created:
        return None, []

    # The same bolt level 1 has: if materialising the paths rewrote a rule file, every verdict in
    # this repo is about a tree that is not the one being described. Crash instead of emitting.
    for d, text in rules.items():
        full = os.path.join(repo_dir, d, ".gitignore") if d else os.path.join(repo_dir,
                                                                             ".gitignore")
        with open(full, encoding="utf-8") as fh:
            if fh.read() != text:
                raise RuntimeError("%s: rule file %r changed while materialising paths" %
                                   (repo, d or "<root>"))

    # ---- three oracles, files only
    paths = [p for p, _, _, _ in created]
    verdict = check_ignore(repo_dir, paths)

    res = git(["status", "--ignored", "--porcelain", "-z"], repo_dir)
    hidden = [e[3:] for e in res.stdout.split("\0") if e.startswith("!! ")]
    hidden_exact = set(hidden)

    def under_hidden(path):
        return any(h.endswith("/") and path.startswith(h) for h in hidden)

    res = git(["add", "-A", "-n"], repo_dir)
    staged = set()
    for line in res.stdout.split("\n"):
        line = line.strip()
        if line.startswith("add '") and line.endswith("'"):
            staged.add(line[5:-1])

    out_q, out_i, meta, bad = [], [], [], []
    for path, cls, owner, pattern in created:
        entry_v = verdict.get(path)
        if entry_v is None:
            continue
        _, a, decided_by = entry_v
        if as_dirs:
            # `status --ignored` names an ignored directory with a trailing slash and does not
            # descend; `add -n` names the file inside, or nothing when the directory is pruned.
            b = path + "/" in hidden_exact or under_hidden(path + "/")
            c = path + "/" + KEEP not in staged
        else:
            b = path in hidden_exact or under_hidden(path)
            c = path not in staged
        if a == b == c:
            out_q.append(path + "/" if as_dirs else path)
            out_i.append(a)
            meta.append({"class": cls, "owner": owner, "from_pattern": pattern,
                         "git_pattern": decided_by, "kind": "dir" if as_dirs else "file"})
        else:
            bad.append({"repo": repo, "path": path, "class": cls,
                        "check_ignore": a, "status": b, "add": c, "pattern": decided_by})

    if not out_q:
        return None, bad
    return ({"repo": repo, "branch": entry.get("branch"), "level": 2,
             "variant": "dirs" if as_dirs else "files",
             "rules": rules, "queries": out_q, "ignored": out_i, "meta": meta}, bad)


def build():
    index = load_index()
    cases, bad, skipped = [], [], []
    for repo in sorted(index):
        got_any = False
        for as_dirs in (False, True):
            workdir = tempfile.mkdtemp(prefix="gic_l2_")
            try:
                case, repo_bad = build_repo_case(repo, index[repo], workdir, as_dirs)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
            bad.extend(repo_bad)
            if case is None:
                continue
            got_any = True
            cases.append(case)
            print("  %-40s %5s %3d rule files taken, %4d cases (%d ignored)" %
                  (repo, case["variant"], len(case["rules"]), len(case["queries"]),
                   sum(case["ignored"])))
        if not got_any:
            skipped.append(repo)
            print("  -- %-40s no layering / no usable query" % repo)

    ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
    # `n_cases` counts requests, `n_repos` counts repositories -- since the 67th session there
    # are two cases per repo (files, directories) and conflating them inflates the census.
    doc = {"level": 2, "oracle": ver, "n_repos": len({c["repo"] for c in cases}),
           "n_cases": len(cases),
           "n_queries": sum(len(c["queries"]) for c in cases),
           "n_excluded": len(bad), "skipped_repos": skipped, "cases": cases}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=False)
    with open(OUT_BAD, "w", encoding="utf-8") as fh:
        json.dump({"n": len(bad), "note": "the three oracles disagreed; not corpus material",
                   "rows": bad[:200]}, fh, indent=1)
    report(doc)


def report(doc=None):
    if doc is None:
        with open(OUT, encoding="utf-8") as fh:
            doc = json.load(fh)
    print()
    print("oracle                  : %s" % doc["oracle"])
    print("repos with level-2 cases: %d   (skipped %d)" % (doc["n_repos"],
                                                           len(doc["skipped_repos"])))
    print("cases (one request each): %d" % doc["n_cases"])
    print("queries                 : %d" % doc.get("n_queries", doc["n_cases"]))
    print("excluded (oracles split): %d" % doc["n_excluded"])
    by_class = {}
    ignored_by_class = {}
    by_kind = {}
    for case in doc["cases"]:
        for m, ig in zip(case["meta"], case["ignored"]):
            key = (m["class"], m.get("kind", "file"))
            by_class[key] = by_class.get(key, 0) + 1
            ignored_by_class[key] = ignored_by_class.get(key, 0) + bool(ig)
            by_kind[key[1]] = by_kind.get(key[1], 0) + 1
    print("by kind                 : %s" % ", ".join("%s %d" % (k, v)
                                                     for k, v in sorted(by_kind.items())))
    print()
    for cls, kind in sorted(by_class):
        n = by_class[(cls, kind)]
        print("  %-10s %-5s %5d queries, %5d ignored (%4.1f%%)" %
              (cls, kind, n, ignored_by_class[(cls, kind)],
               100.0 * ignored_by_class[(cls, kind)] / n))

    # The measurement the whole level exists for: a rule that decides inside its own subtree and
    # does NOT decide the same leaf next door. One of these is a bug in every tool that keeps one
    # flat list of rules.
    seams = 0
    seam_repos = set()
    for case in doc["cases"]:
        idx = {q: (i, m) for q, (i, m) in zip(case["queries"],
                                              zip(case["ignored"], case["meta"]))}
        for q, (ig, m) in idx.items():
            if m["class"] != "own" or not ig:
                continue
            leaf = q[len(m["owner"]) + 1:]
            other = idx.get(leaf)
            if other and other[1]["class"] == "root" and other[0] is False:
                seams += 1
                seam_repos.add(case["repo"])
    print()
    print("subtree seams (own=ignored, same leaf at root=not): %d in %d repos" %
          (seams, len(seam_repos)))
    print("  " + ", ".join(sorted(seam_repos)[:8]))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        build()
