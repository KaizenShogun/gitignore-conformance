#!/usr/bin/env python3
"""Level-2 corpus: the *tree* of rule files, not one file's worth of lines.

Level 1 asks a pattern-matching library about one `.gitignore`. Level 2 asks whatever walks the
repository which paths are ignored given every `.gitignore` in it, each scoped to its own subtree.
That is a different question and it needs different raw material: not the root file, but all of
them, with the directory each one lives in.

This script does the network half, in two passes, both cached on disk so a rerun is free:

  survey  -- one GitHub API call per repo for the recursive tree, keep the paths that end in
             `.gitignore`, then fetch each one from raw.githubusercontent (no API budget spent).
  report  -- read the cache and print what is there: how many rule files per repo, how deep, and
             which repos have no nesting at all.

The oracle half (materialise the tree, ask git about paths that cross subtree boundaries) lives
in `build_corpus.py`'s vocabulary and is deliberately not here yet -- it needs no network, so it
does not belong in the same run as 43 HTTP requests.

Rate limit, said out loud because it bit an earlier session: the unauthenticated search API allows
60 requests an hour and this uses 43 of them in one pass. If it 403s, the cache keeps whatever
arrived and the next run picks up where it stopped.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_corpus import REPOS, UA  # noqa: E402  -- one list of repos, not two

CACHE = os.path.join(HERE, "corpus", "_cache_l2")
# A tree of 400k entries is not a corpus, it is a download. Repos past this are recorded as
# truncated and their rule files are taken from whatever the API did return.
API = "https://api.github.com/repos/%s/git/trees/%s?recursive=1"
RAW = "https://raw.githubusercontent.com/%s/%s/%s"


def get(url, timeout=45):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def fetch_tree_on(repo, branch, key):
    """One API call. Raises on HTTP error so the caller can tell 404 (wrong branch) from 403."""
    data = json.loads(get(API % (repo, branch)).decode("utf-8"))
    out = {
        "repo": repo,
        "branch": branch,
        "truncated": bool(data.get("truncated")),
        "n_entries": len(data.get("tree", [])),
        "ignorefiles": sorted(e["path"] for e in data.get("tree", [])
                              if e.get("type") == "blob"
                              and os.path.basename(e["path"]) == ".gitignore"),
    }
    os.makedirs(CACHE, exist_ok=True)
    with open(key, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    time.sleep(0.6)
    return out


def fetch_tree(repo):
    """[(path, mode)] for every blob in the default branch, plus the branch name and truncation."""
    key = os.path.join(CACHE, repo.replace("/", "__") + ".tree.json")
    if os.path.exists(key):
        with open(key, encoding="utf-8") as fh:
            return json.load(fh)
    last = None
    branches = ["main", "master"]
    # `next.js` lives on `canary` and `ansible` on `devel`. Guessing main/master silently drops
    # them, which is how a "43 repos" corpus quietly becomes 41 -- so ask the API which branch it
    # actually is, and spend the extra call only when the guess failed.
    for branch in branches:
        try:
            out = fetch_tree_on(repo, branch, key)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 403:
                raise
            continue
        except Exception as exc:  # noqa: BLE001 -- network, and the caller wants to keep going
            print("  !! %s: %s" % (repo, exc), file=sys.stderr)
            return None
        return out
    try:
        meta = json.loads(get("https://api.github.com/repos/" + repo).decode("utf-8"))
        default = meta.get("default_branch")
    except Exception as exc:  # noqa: BLE001
        print("  !! %s: no main/master (%s), and no default_branch (%s)" % (repo, last, exc),
              file=sys.stderr)
        return None
    if default and default not in branches:
        branches.append(default)
        return fetch_tree_on(repo, default, key)
    print("  !! %s: no main/master (%s)" % (repo, last), file=sys.stderr)
    return None


def fetch_rule_file(repo, branch, path):
    """The lines of one `.gitignore`, verbatim. Cached under a flattened name."""
    flat = repo.replace("/", "__") + "@@" + path.replace("/", "__")
    key = os.path.join(CACHE, flat)
    if os.path.exists(key):
        with open(key, encoding="utf-8") as fh:
            return fh.read()
    try:
        text = get(RAW % (repo, branch, path)).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print("  !! %s:%s: %s" % (repo, path, exc), file=sys.stderr)
        return None
    os.makedirs(CACHE, exist_ok=True)
    with open(key, "w", encoding="utf-8") as fh:
        fh.write(text)
    time.sleep(0.25)
    return text


def survey(limit_rule_files=40):
    """Fetch trees and rule files. Returns {repo: {...}} and writes the index to disk."""
    index = {}
    blocked = False
    for repo in REPOS:
        cached = os.path.join(CACHE, repo.replace("/", "__") + ".tree.json")
        if blocked and not os.path.exists(cached):
            print("  -- %s: skipped, rate limited and not cached" % repo, file=sys.stderr)
            continue
        try:
            tree = fetch_tree(repo)
        except urllib.error.HTTPError as exc:
            # Do NOT stop the pass: the repos after this one may well be on disk already, and
            # dropping them would rewrite the index with fewer entries than the cache holds --
            # a survey that shrinks when the network hiccups is worse than one that stops.
            print("  !! rate limit at %s (%s). Continuing from cache only." % (repo, exc),
                  file=sys.stderr)
            blocked = True
            continue
        if tree is None:
            continue
        rules = {}
        # Deterministic order, and the root first: if a repo has 300 nested rule files we take a
        # prefix, and taking it in path order beats taking it at random.
        for path in tree["ignorefiles"][:limit_rule_files]:
            text = fetch_rule_file(repo, tree["branch"], path)
            if text is None:
                continue
            directory = os.path.dirname(path)  # "" for the root file, which is what level 2 wants
            rules[directory] = text
        tree["n_rule_files_total"] = len(tree["ignorefiles"])
        tree["n_rule_files_taken"] = len(rules)
        tree["dirs"] = sorted(rules)
        index[repo] = tree
        print("  %-40s %3d rule files (%s%s)" % (
            repo, len(tree["ignorefiles"]), tree["branch"],
            ", TRUNCATED tree" if tree["truncated"] else ""))
    with open(os.path.join(CACHE, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
    return index


def report():
    with open(os.path.join(CACHE, "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    counts = sorted((v["n_rule_files_total"], k) for k, v in index.items())
    nested = [c for c, _ in counts if c > 1]
    print("repos surveyed          : %d" % len(index))
    print("with >1 rule file       : %d" % len(nested))
    print("root-only (no layering) : %s" % ", ".join(k for c, k in counts if c <= 1))
    if counts:
        mid = counts[len(counts) // 2][0]
        print("median rule files/repo  : %d   (min %d, max %d)" % (mid, counts[0][0], counts[-1][0]))
    print("truncated trees         : %s" % ", ".join(k for k, v in index.items()
                                                     if v["truncated"]) or "none")
    print()
    for count, repo in reversed(counts):
        depth = max((d.count("/") + 1 if d else 0) for d in index[repo]["dirs"]) \
            if index[repo]["dirs"] else 0
        print("  %-40s %4d files, deepest rule dir %d level(s) down" % (repo, count, depth))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "survey"
    if mode == "survey":
        survey()
        print()
        report()
    else:
        report()
