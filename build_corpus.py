#!/usr/bin/env python3
"""Build the frozen corpus: real .gitignore files, real files on disk, git as the oracle.

This is the only script in the project that needs the network and a working git. Everything else
runs offline against the JSON it produces.

How a case gets in:

  1. Fetch the root .gitignore of a well-known repository.
  2. Derive candidate paths from that file's own patterns, plus near misses of those paths.
  3. Create every candidate as a real file inside a real `git init` repository.
  4. Ask git three separate ways whether each path is ignored:
        A. `git check-ignore -v --non-matching --stdin`   -> deciding pattern
        B. `git status --ignored --porcelain`             -> what git hides in the working tree
        C. `git add -A -n`                                -> what git would actually stage
  5. Keep the case only if all three agree. Disagreements are recorded, counted and dropped,
     never guessed at.

Two things that cost me a probe script each, and that are the reason this file exists at all:

  * `check-ignore` exiting 0 does NOT mean "ignored". It means "some pattern decided this path".
    With `.gitignore` = `*` / `!*/`, `git check-ignore -v sub/` exits 0 and names `!*/` -- a
    negation, so the answer is *not ignored*. Reading the exit code as the verdict silently
    inverts every negation in the corpus.
  * `!! d/` in `status --ignored` does NOT mean "d/ is ignored". A directory whose contents are
    all ignored is collapsed the same way. So B is not a directory oracle; it is "git has nothing
    left to offer under here". Directory cases state the triple rule accordingly (see `oracle`).
  * Asking `check-ignore` about `d/` -- with the trailing slash -- is not the same question as
    asking about `d`. The pattern `d/*` matches the string `d/`, because `*` matches empty. An
    earlier version of this file asked with the slash and recorded every `foo/*` directory as
    ignored; three separate libraries then "failed" the same twenty cases, which is what a
    broken bench looks like from the outside. Directories are asked bare, against the real tree.

Usage:  python3 build_corpus.py [--out corpus/cases.json] [--cache DIR]
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPOS = [
    "facebook/react", "vuejs/core", "angular/angular", "sveltejs/svelte",
    "microsoft/vscode", "nodejs/node", "denoland/deno", "vercel/next.js",
    "django/django", "pallets/flask", "psf/black", "python/cpython",
    "pandas-dev/pandas", "numpy/numpy", "scikit-learn/scikit-learn", "pytorch/pytorch",
    "tensorflow/tensorflow", "huggingface/transformers", "langchain-ai/langchain",
    "rust-lang/rust", "golang/go", "kubernetes/kubernetes", "docker/compose",
    "ansible/ansible", "hashicorp/terraform", "elastic/elasticsearch",
    "grafana/grafana", "prometheus/prometheus", "home-assistant/core",
    "apache/airflow", "streamlit/streamlit", "gradio-app/gradio",
    "tailwindlabs/tailwindcss", "expressjs/express", "electron/electron",
    "obsidianmd/obsidian-releases", "supabase/supabase", "ollama/ollama",
    "openai/openai-python", "anthropics/anthropic-sdk-python",
    "kirill-markin/repo-to-text", "simonw/files-to-prompt", "cpburnz/python-pathspec",
]

UA = {"User-Agent": "gitignore-conformance corpus builder"}
MAX_FILES_PER_REPO = 260
MAX_DIRS_PER_REPO = 60


# --------------------------------------------------------------------------- fetching

def fetch_gitignore(repo, cache):
    """Return (text, url) or (None, None). Cached on disk so a rebuild is cheap and offline-ish."""
    os.makedirs(cache, exist_ok=True)
    key = os.path.join(cache, repo.replace("/", "__"))
    meta = key + ".url"
    if os.path.exists(key):
        with open(key, encoding="utf-8") as fh:
            text = fh.read()
        url = ""
        if os.path.exists(meta):
            with open(meta, encoding="utf-8") as fh:
                url = fh.read().strip()
        return (text, url) if text.strip() else (None, None)
    for branch in ("main", "master"):
        url = "https://raw.githubusercontent.com/%s/%s/.gitignore" % (repo, branch)
        try:
            req = urllib.request.Request(url, headers=UA)
            text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            continue
        except Exception as exc:
            print("  !! %s: %s" % (repo, exc), file=sys.stderr)
            return None, None
        with open(key, "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(meta, "w", encoding="utf-8") as fh:
            fh.write(url)
        time.sleep(0.4)
        return text, url
    with open(key, "w", encoding="utf-8") as fh:
        fh.write("")
    return None, None


# --------------------------------------------------------------------------- candidates

def split_patterns(text):
    """Positive file patterns, directory-only patterns, negations. Comments and blanks dropped."""
    dirs, files, negs = [], [], []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            negs.append(line[1:])
        elif line.endswith("/"):
            dirs.append(line)
        else:
            files.append(line)
    return dirs, files, negs


def concretize(pattern):
    """Turn a pattern into one concrete path, or None if it cannot be done honestly.

    Character classes are skipped rather than guessed at: expanding `[abc]` myself would put my
    reading of the spec into the corpus, and the corpus is supposed to contain git's reading.
    A literal backslash is dropped for the same reason -- in an earlier round my own generator
    invented paths with literal backslashes that no repository has, and I nearly reported them.
    """
    if "[" in pattern or "]" in pattern or "\\" in pattern:
        return None
    body = pattern.replace("**/", "").replace("**", "x").replace("*", "sample").replace("?", "a")
    body = body.strip("/").strip()
    if not body or body.startswith("#") or ".." in body:
        return None
    if len(body) > 120 or any(c in body for c in "\n\r\t\0:"):
        return None
    if any(part in ("", ".", "..") for part in body.split("/")):
        return None
    return body


def near_misses(body):
    """Paths that live next door to a matching one. git decides; this only proposes neighbours.

    Without these the corpus is 95% "ignored" and an adapter that answers True to everything
    looks conforming. A near miss is where anchoring and directory semantics actually break:
    `/build` vs `pkg/build`, `*.log` vs `notes.log.keep`.
    """
    out = []
    base = os.path.basename(body)
    parent = os.path.dirname(body)

    def join(p, b):
        return "%s/%s" % (p, b) if p else b

    out.append(body + ".keepme")            # longer leaf: does the pattern anchor at the end?
    out.append(join(parent, "keep" + base))  # longer stem: does it anchor at the start?
    out.append("vendor/" + body)            # same thing one level deeper
    if parent:
        out.append(base)                    # same leaf with the directory peeled off
    stem, ext = os.path.splitext(base)
    if ext and stem:
        out.append(join(parent, stem + ".keepme"))
    return out


def candidate_paths(text):
    """Paths worth asking about, derived from this repo's own patterns.

    Every path exercises a pattern that a real project actually wrote. The path itself is
    synthesised -- that distinction is stated in the README and it matters. Categories are
    interleaved so the per-repo cap does not silently drop a whole class of question.
    """
    dirs, files, negs = split_patterns(text)
    buckets = {"dir": [], "file": [], "unanchored": [], "negation": [], "near": []}
    seen = set()

    def add(bucket, path):
        path = path.strip("/")
        if path and ".." not in path and path not in seen:
            seen.add(path)
            buckets[bucket].append(path)

    for pattern in dirs:
        body = concretize(pattern)
        if not body:
            continue
        add("dir", "%s/x.txt" % body)
        add("dir", "%s/pkg/x.txt" % body)
        for nm in near_misses(body):
            add("near", "%s/x.txt" % nm)
    for pattern in files:
        body = concretize(pattern)
        if not body:
            continue
        add("file", body)
        if "/" not in pattern.strip("/"):
            add("unanchored", "pkg/%s" % body)        # does an unanchored pattern reach down?
            add("unanchored", "pkg/deep/%s" % body)   # ...and two levels? (Nesbitt's go-git case)
        for nm in near_misses(body):
            add("near", nm)
    for pattern in negs:
        body = concretize(pattern)
        if not body:
            continue
        add("negation", body)
        if "/" not in pattern.strip("/"):
            add("negation", "pkg/%s" % body)
        for d in dirs[:8]:                            # re-inclusion under an excluded directory
            dbody = concretize(d)
            if dbody and not body.startswith(dbody + "/"):
                add("negation", "%s/%s" % (dbody, os.path.basename(body)))

    out, order = [], ["file", "dir", "negation", "unanchored", "near"]
    i = 0
    while len(out) < MAX_FILES_PER_REPO:
        drained = True
        for name in order:
            if i < len(buckets[name]):
                out.append(buckets[name][i])
                drained = False
                if len(out) >= MAX_FILES_PER_REPO:
                    break
        if drained:
            break
        i += 1
    return out


# --------------------------------------------------------------------------- the oracle

def git(args, cwd, stdin=None):
    return subprocess.run(["git"] + args, cwd=cwd, input=stdin, capture_output=True,
                          text=True, timeout=300)


def check_ignore(repo, queries):
    """{path: (decided, ignored, pattern)} from `check-ignore -v --non-matching`.

    `decided` is "a pattern had something to say". `ignored` is that pattern not being a negation.
    Conflating the two is the mistake this whole function exists to avoid.
    """
    res = git(["check-ignore", "-v", "--non-matching", "--stdin"], repo,
              stdin="\n".join(queries) + "\n")
    out = {}
    for line in res.stdout.split("\n"):
        if not line.strip():
            continue
        head, _, path = line.rpartition("\t")
        if not path:
            continue
        fields = head.split(":")
        decided = bool(head) and fields[0] != ""
        pattern = ":".join(fields[2:]) if decided and len(fields) >= 3 else None
        ignored = decided and not (pattern or "").startswith("!")
        out[path] = (decided, ignored, pattern)
    return out


CANARY = "gic_canary_probe"


def reinclusion_probe(gitignore_text, dirs, workdir):
    """For each directory, does git let a negation re-include a file inside it?

    This is the only sound second opinion I found for directory questions, and it exists
    because the obvious ones are not opinions at all:

      * `check-ignore d/` -- with a trailing slash -- matches the pattern `d/*`, because in
        wildmatch `*` happily matches the empty string. That is my question creating its own
        answer, not git ignoring the directory.
      * `!! d/` in `status --ignored` collapses "this directory is ignored" together with
        "everything inside it happens to be ignored". Those are different facts.
      * "nothing staged under d/" is true of both as well.
    All three fire together on `d/*`, so requiring them to agree buys nothing.

    The probe asks git the question by its consequence instead, using the rule from
    gitignore(5): "It is not possible to re-include a file if a parent directory of that file
    is excluded." Drop a canary in each directory, append a negation for it, and see what git
    would stage. Staged means the parent was never excluded. This separates `d/*` (staged --
    the whole reason the idiom exists) from `d/` (not staged), which nothing else here does.

    Returns {dir: True if a canary inside it can be re-included}.
    """
    repo = os.path.join(workdir, "probe")
    os.makedirs(repo)
    git(["init", "-q"], repo)
    git(["config", "user.email", "corpus@example.invalid"], repo)
    git(["config", "user.name", "corpus"], repo)

    usable = [d for d in dirs if d.split("/")[0] != ".git"]
    rules = list(gitignore_text.split("\n"))
    for d in usable:
        rules.append("!%s/%s" % (d, CANARY))
    with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rules) + "\n")

    made = []
    for d in usable:
        full = os.path.join(repo, d, CANARY)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        except (OSError, ValueError):
            continue
        made.append(d)

    res = git(["add", "-A", "-n"], repo)
    staged = set()
    for line in res.stdout.split("\n"):
        line = line.strip()
        if line.startswith("add '") and line.endswith("'"):
            staged.add(line[5:-1])
    return {d: ("%s/%s" % (d, CANARY)) in staged for d in made}


def oracle(gitignore_text, paths, workdir):
    """Materialise the paths, ask git, and return (cases, disagreements).

    Files use the plain triple rule: check-ignore, status and add must give the same answer.

    Directories are asked WITHOUT a trailing slash -- the tree is real, so git stats the path
    and applies directory-only patterns itself, which is the whole reason for building a tree
    instead of reasoning about strings. Their three answers are:
        A. `check-ignore -v` on the bare path            -> deciding pattern
        B. the re-inclusion probe above                   -> is the directory really excluded?
        C. nothing staged anywhere under it               -> one-directional falsifier
    A and B must agree, and C must not contradict them. Anything else is dropped and counted.
    """
    repo = os.path.join(workdir, "r")
    os.makedirs(repo)
    git(["init", "-q"], repo)
    git(["config", "user.email", "corpus@example.invalid"], repo)
    git(["config", "user.name", "corpus"], repo)
    with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write(gitignore_text)

    created = []
    for path in paths:
        # Never write over the file being measured. `.gitignore` is a candidate path in any
        # repository whose own rules mention it -- `!.gitignore` is a common idiom -- and
        # materialising it as "x" replaces the rules with the single word `x`. git then honestly
        # reports that nothing is ignored, every verdict in that repo comes back False with no
        # pattern to blame, and the corpus looks fine. It cost me 58 poisoned cases and three
        # innocent libraries convicted of the same 39 failures before I noticed.
        if path == ".gitignore":
            created.append(path)          # already on disk, holding the real rules
            continue
        if path.split("/")[0] == ".git":
            continue                      # inside git's own directory; not a case
        full = os.path.join(repo, path)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            # A nested .gitignore is a rules file too. Create it empty: it stays a real,
            # askable path and contributes no patterns of its own.
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("" if os.path.basename(path) == ".gitignore" else "x\n")
        except (OSError, ValueError):
            continue                      # unrepresentable on this filesystem; not a case
        created.append(path)
    if not created:
        return [], []

    # The bolt on the above. Cheap, and it turns a silent corpus-wide poisoning into a crash.
    with open(os.path.join(repo, ".gitignore"), encoding="utf-8") as fh:
        on_disk = fh.read()
    if on_disk != gitignore_text:
        raise RuntimeError("the root .gitignore was modified while materialising paths; "
                           "the oracle would be measuring a different file than the one asked "
                           "about. Refusing to emit cases.")

    # every directory that now really exists, deepest last, capped
    dirs, seen = [], set()
    for path in created:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d and d not in seen:
                seen.add(d)
                dirs.append(d)
    dirs = dirs[:MAX_DIRS_PER_REPO]

    # Directories go in bare: the tree is real, so git decides directory-ness by looking.
    verdict_a = check_ignore(repo, created + dirs)
    reincludable = reinclusion_probe(gitignore_text, dirs, workdir)

    res = git(["status", "--ignored", "--porcelain", "-z"], repo)
    hidden = [e[3:] for e in res.stdout.split("\0") if e.startswith("!! ")]
    hidden_exact = set(hidden)

    def under_hidden(path):
        return any(h.endswith("/") and path.startswith(h) for h in hidden)

    res = git(["add", "-A", "-n"], repo)
    staged = set()
    for line in res.stdout.split("\n"):
        line = line.strip()
        if line.startswith("add '") and line.endswith("'"):
            staged.add(line[5:-1])

    cases, bad = [], []

    for path in created:
        entry = verdict_a.get(path)
        if entry is None:
            continue
        _, a, pattern = entry
        b = path in hidden_exact or under_hidden(path)
        c = path not in staged
        if a == b == c:
            cases.append({"path": path, "kind": "file", "ignored": a, "pattern": pattern})
        else:
            bad.append({"path": path, "kind": "file", "check_ignore": a, "status": b,
                        "add": c, "pattern": pattern})

    for d in dirs:
        entry = verdict_a.get(d)
        probe = reincludable.get(d)
        if entry is None or probe is None:
            continue
        _, a, pattern = entry
        b = not probe                                     # excluded, per the re-inclusion probe
        anything_staged = any(s.startswith("%s/" % d) for s in staged)
        # The case is stored with the trailing slash, because that is how an adapter -- which
        # has no filesystem to stat -- is told the path is a directory. See PROTOCOL.md.
        if a == b and not (a and anything_staged):
            cases.append({"path": "%s/" % d, "kind": "dir", "ignored": a, "pattern": pattern})
        else:
            bad.append({"path": "%s/" % d, "kind": "dir", "check_ignore": a,
                        "reincludable": probe, "staged_below": anything_staged,
                        "pattern": pattern})

    return cases, bad


# --------------------------------------------------------------------------- driver

def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--out", default=os.path.join(here, "corpus", "cases.json"))
    ap.add_argument("--excluded", default=os.path.join(here, "corpus", "excluded.json"))
    ap.add_argument("--provenance", default=os.path.join(here, "corpus", "PROVENANCE.md"))
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "gic-gitignores"))
    args = ap.parse_args()

    if not shutil.which("git"):
        sys.exit("git not found; the corpus is built from git's own answers and nothing else.")

    cases, excluded, provenance = [], [], []

    for repo in REPOS:
        text, url = fetch_gitignore(repo, args.cache)
        if not text:
            print("  -- %s: no root .gitignore" % repo)
            continue
        paths = candidate_paths(text)
        if not paths:
            continue
        workdir = tempfile.mkdtemp(prefix="gic-")
        try:
            kept, bad = oracle(text, paths, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        for c in kept:
            c["repo"] = repo
            cases.append(c)
        for b in bad:
            b["repo"] = repo
            excluded.append(b)

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        provenance.append({
            "repo": repo, "url": url, "sha256": digest,
            "patterns": text.split("\n"),
            "lines": len(text.split("\n")), "asked": len(paths),
            "kept": len(kept), "dropped": len(bad),
        })
        print("  %-42s %4d kept (%3d dirs, %3d not-ignored)  %3d dropped" %
              (repo, len(kept), sum(1 for c in kept if c["kind"] == "dir"),
               sum(1 for c in kept if not c["ignored"]), len(bad)))

    cases.sort(key=lambda c: (c["repo"], c["path"]))
    corpus = {
        "version": 1,
        "oracle": "git " + git(["--version"], ".").stdout.strip().split()[-1],
        "note": ("Verdicts are git's own, taken from real files in a real working tree. A file "
                 "case is kept only when check-ignore, status --ignored and add -n agree. A "
                 "directory case is asked bare (never with a trailing slash, which would let "
                 "`d/*` match it via the empty string) and is kept only when check-ignore and a "
                 "re-inclusion probe agree and nothing staged below contradicts them. See README."),
        # the patterns travel with the corpus: a consumer needs no network and no git, only this file
        "gitignores": {p["repo"]: {"url": p["url"], "sha256": p["sha256"],
                                   "patterns": p["patterns"]} for p in provenance},
        "cases": cases,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(corpus, fh, indent=1, sort_keys=True)
        fh.write("\n")
    with open(args.excluded, "w", encoding="utf-8") as fh:
        json.dump({"note": "Paths where git's three answers did not agree. Dropped, never guessed.",
                   "excluded": excluded}, fh, indent=1, sort_keys=True)
        fh.write("\n")

    with open(args.provenance, "w", encoding="utf-8") as fh:
        fh.write("# Where the corpus comes from\n\n")
        fh.write("Every case below is derived from the root `.gitignore` of a real repository, and "
                 "every verdict is git's own, taken from a real path in a real working tree.\n\n")
        fh.write("| repository | .gitignore lines | paths asked | cases kept | dropped | sha256 |\n")
        fh.write("|---|---:|---:|---:|---:|---|\n")
        for p in sorted(provenance, key=lambda x: x["repo"]):
            fh.write("| [`%s`](%s) | %d | %d | %d | %d | `%s` |\n" %
                     (p["repo"], p["url"], p["lines"], p["asked"], p["kept"], p["dropped"],
                      p["sha256"][:16]))
        fh.write("\nThe sha256 is of the `.gitignore` text as fetched. If upstream edits their "
                 "file the hash stops matching and the corpus needs rebuilding -- that is the "
                 "point of recording it.\n")

    ign = sum(1 for c in cases if c["ignored"])
    dirs = sum(1 for c in cases if c["kind"] == "dir")
    print("\n%d repositories, %d cases: %d ignored / %d not, %d directories."
          % (len(provenance), len(cases), ign, len(cases) - ign, dirs))
    print("%d paths dropped because git's three answers disagreed (see %s)."
          % (len(excluded), os.path.basename(args.excluded)))
    print("written: %s" % args.out)


if __name__ == "__main__":
    main()
