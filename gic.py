#!/usr/bin/env python3
"""Run a `.gitignore` implementation against the frozen corpus and report where it leaves git.

    gic.py [options] -- <command that speaks the adapter protocol>

    gic.py -- python3 adapters/pathspec_adapter.py
    gic.py --kind dir -- node adapters/node_ignore_adapter.js
    gic.py --repo python/cpython --limit 20 -- ./my-adapter

Output is the failing path, the pattern git blamed and the repository the `.gitignore` came from.
Not a conformance percentage: a percentage tells you how you feel, a path tells you what to fix.

Exit status: 0 if the implementation agreed with git everywhere, 1 otherwise, 2 on a broken
adapter. Paths the adapter declined to answer (`null`) are counted and shown separately -- an
honest "I don't implement that" is not a divergence, and lumping the two together would be the
kind of number-massaging this tool exists to make unnecessary.

The protocol is in PROTOCOL.md and fits on one page. Any language that can read a line of JSON
from stdin and write a line of JSON to stdout can be measured here.
"""
import argparse
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CORPUS = os.path.join(HERE, "corpus", "cases.json")


def die(message):
    """Exit 2. Reserved for "I could not run the measurement at all", never for a failing run.

    Keeping this distinct from 1 is the difference between CI saying "your library diverges"
    and CI saying "your adapter is broken", and a script that cannot tell those apart will
    eventually report a typo as conformance.
    """
    sys.stderr.write("gic: %s\n" % message)
    raise SystemExit(2)


class Adapter:
    """One long-lived subprocess speaking newline-delimited JSON."""

    def __init__(self, argv):
        self.argv = argv
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=None, text=True, bufsize=1)
        except OSError as exc:
            die("cannot start adapter %r: %s" % (" ".join(argv), exc))

    def ask(self, request_id, patterns, queries):
        payload = json.dumps({"id": request_id, "patterns": patterns, "queries": queries})
        try:
            self.proc.stdin.write(payload + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        except (BrokenPipeError, ValueError):
            line = ""
        if not line:
            die("adapter died or closed stdout while answering request %d "
                "(%d queries). Its stderr is above." % (request_id, len(queries)))
        try:
            reply = json.loads(line)
        except ValueError:
            die("adapter wrote something that is not JSON:\n  %s" % line[:400].rstrip())
        if reply.get("id") != request_id:
            die("adapter answered request %r while %r was asked; the protocol is "
                "one reply per request, in order." % (reply.get("id"), request_id))
        answers = reply.get("ignored")
        if not isinstance(answers, list) or len(answers) != len(queries):
            die("adapter returned %s answers for %d queries; they must line up "
                "one-to-one, in order." %
                (len(answers) if isinstance(answers, list) else "non-list", len(queries)))
        return answers

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def load_corpus(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        die("no corpus at %s -- run build_corpus.py, or pass --corpus." % path)


def select(corpus, kind, repo, limit):
    cases = corpus["cases"]
    if kind:
        cases = [c for c in cases if c["kind"] == kind]
    if repo:
        cases = [c for c in cases if c["repo"] == repo]
    if limit:
        cases = cases[:limit]
    return cases


def run(corpus, cases, adapter):
    """Ask the adapter every case, grouped one request per .gitignore. Returns (bad, skipped)."""
    grouped = collections.OrderedDict()
    for case in cases:
        grouped.setdefault(case["repo"], []).append(case)

    divergences, declined = [], []
    for request_id, (repo, group) in enumerate(grouped.items()):
        meta = corpus["gitignores"].get(repo)
        if meta is None:
            die("corpus is missing the patterns for %s; rebuild it." % repo)
        answers = adapter.ask(request_id, meta["patterns"], [c["path"] for c in group])
        for case, got in zip(group, answers):
            if got is None:
                declined.append(case)
            elif bool(got) != case["ignored"]:
                divergences.append((case, bool(got)))
    return divergences, declined


def report(divergences, declined, total, args):
    if args.json:
        json.dump({
            "checked": total,
            "divergences": [{"repo": c["repo"], "path": c["path"], "kind": c["kind"],
                             "pattern": c["pattern"], "git": c["ignored"], "adapter": got}
                            for c, got in divergences],
            "declined": [{"repo": c["repo"], "path": c["path"]} for c in declined],
        }, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return

    if divergences:
        print("%d divergence%s from git in %d cases:\n"
              % (len(divergences), "" if len(divergences) == 1 else "s", total))
        by_pattern = collections.Counter()
        for case, got in divergences[:args.show]:
            print("  %s" % case["path"])
            print("      git says %s, adapter says %s" %
                  ("ignored" if case["ignored"] else "not ignored",
                   "ignored" if got else "not ignored"))
            print("      pattern:   %s" % (case["pattern"] if case["pattern"]
                                           else "(none -- git found nothing that matched)"))
            print("      from:      %s\n" % case["repo"])
        if len(divergences) > args.show:
            print("  ... and %d more (--show N to see them, --json for all of it)\n"
                  % (len(divergences) - args.show))
        for case, _ in divergences:
            by_pattern[case["pattern"] or "(no matching pattern)"] += 1
        print("  most common guilty patterns:")
        for pattern, count in by_pattern.most_common(8):
            print("    %4d  %s" % (count, pattern))
    else:
        print("no divergences: %d cases, all of them git's answer." % total)

    if declined:
        print("\n%d case%s the adapter declined to answer (reported, not counted as failures):"
              % (len(declined), "" if len(declined) == 1 else "s"))
        for case in declined[:5]:
            print("    %s  (%s)" % (case["path"], case["repo"]))
        if len(declined) > 5:
            print("    ... and %d more" % (len(declined) - 5))


def main():
    ap = argparse.ArgumentParser(
        usage="gic.py [options] -- <adapter command>",
        description="Differential conformance bench for .gitignore implementations.")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--kind", choices=("file", "dir"), help="only file or only directory queries")
    ap.add_argument("--repo", help="only cases from this repository's .gitignore")
    ap.add_argument("--limit", type=int, help="stop after this many cases (for a quick smoke run)")
    ap.add_argument("--show", type=int, default=20, help="how many divergences to print in full")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not argv:
        ap.error("give me an adapter to run, after --. See PROTOCOL.md.")

    corpus = load_corpus(args.corpus)
    cases = select(corpus, args.kind, args.repo, args.limit)
    if not cases:
        die("that selection matches no cases.")

    adapter = Adapter(argv)
    try:
        divergences, declined = run(corpus, cases, adapter)
    finally:
        adapter.close()

    report(divergences, declined, len(cases), args)
    return 1 if divergences else 0


if __name__ == "__main__":
    sys.exit(main())
