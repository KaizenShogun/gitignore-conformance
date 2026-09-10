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
DEFAULT_CORPUS_L2 = os.path.join(HERE, "corpus", "cases_l2.json")

# The five ways a path can relate to the rule file that speaks about it. The order is the
# order they get harder: `own` is what a flat list of patterns already gets right, `sibling`
# and `root` are what it cannot.
CLASSES = ("own", "deeper", "from_root", "sibling", "root")

# The `exclude` variant asks a different question -- about `.git/info/exclude`, a rule file that
# is not a `.gitignore` -- so its classes live apart and its numbers never join the ones above.
# Only `exclude_only` is decisive: the other two keep git's verdict when the field is removed,
# so against a subject that never opens the file they measure nothing. See `decisive` in the meta.
EXCLUDE_CLASSES = ("exclude_only", "exclude_overridden", "exclude_order")
ALL_CLASSES = CLASSES + EXCLUDE_CLASSES


def die(message):
    """Exit 2. Reserved for "I could not run the measurement at all", never for a failing run.

    Keeping this distinct from 1 is the difference between CI saying "your library diverges"
    and CI saying "your adapter is broken", and a script that cannot tell those apart will
    eventually report a typo as conformance.
    """
    sys.stderr.write("gic: %s\n" % message)
    raise SystemExit(2)


def _lines(text):
    """Rule-file contents as a list of lines, which is what the protocol promises.

    Level 1 has always sent `patterns` as a list; the level-2 corpus stores each rule file as
    one newline-joined string, and until now `ask_l2` forwarded that string untouched. Both my
    adapters happened to accept either shape, so the bench never noticed -- but an adapter
    written from PROTOCOL.md would do `for line in rules[dir]` and iterate 878 characters
    instead of 40 lines, and answer confidently. Normalising here means the two levels put the
    same shape on the wire and the page is true about both.
    """
    if isinstance(text, list):
        return text
    return text.split("\n")


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
        return self._roundtrip(request_id, {"id": request_id, "patterns": patterns,
                                            "queries": queries}, queries)

    def _roundtrip(self, request_id, request, queries):
        payload = json.dumps(request)
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

    def ask_l2(self, request_id, rules, exclude, queries):
        """Same conversation, level-2 request shape. An all-null reply means "I only do level 1"."""
        payload = {"id": request_id, "level": 2,
                   "rules": {d: _lines(v) for d, v in rules.items()}, "queries": queries}
        if exclude:
            payload["exclude"] = _lines(exclude)
        return self._roundtrip(request_id, payload, queries)

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


def run_l2(cases, adapter):
    """One request per repository: its whole tree of rule files, all its queries.

    Returns (divergences, declined, declining_repos). A repo that answered `null` to every
    single query has declined level 2 outright -- that is the honest answer from a level-1
    library and it is reported as such, not as 2.224 failures.
    """
    divergences, declined, declining = [], [], []
    for request_id, case in enumerate(cases):
        answers = adapter.ask_l2(request_id, case["rules"], case.get("exclude"),
                                 case["queries"])
        if all(a is None for a in answers) and answers:
            # Key on the case, not the repo: a repo now has two of them (files and directories),
            # and an adapter that declines directories outright still answers the file case.
            declining.append((case["repo"], case.get("variant", "files")))
            continue
        for query, truth, meta, got in zip(case["queries"], case["ignored"],
                                           case["meta"], answers):
            record = dict(meta, repo=case["repo"], path=query, ignored=truth)
            if got is None:
                declined.append(record)
            elif bool(got) != bool(truth):
                divergences.append((record, bool(got)))
    return divergences, declined, declining


def report_l2(divergences, declined, declining, cases, args):
    """The per-class table. A single conformance number would hide the entire finding here:
    a tool with one flat list of rules can be perfect on `own` and still wrong on `sibling`."""
    # A repo that declined level 2 in one block never answered anything, so its queries are
    # not part of any denominator. Counting them made the printed total lie by 45 against
    # black, which declines its own repository because of a `.gitignore` its test suite ships.
    declining_repos = set(declining)
    answered = [c for c in cases
                if (c["repo"], c.get("variant", "files")) not in declining_repos]
    totals = collections.Counter()
    decisive = collections.Counter()
    for case in answered:
        for meta in case["meta"]:
            totals[meta["class"]] += 1
            # `decisive` is absent from the two shipped variants: there every query moves with
            # the rule files, so the flag would be True everywhere and says nothing.
            if meta.get("decisive", True):
                decisive[meta["class"]] += 1
    bad = collections.Counter(rec["class"] for rec, _ in divergences)
    bad_decisive = collections.Counter(rec["class"] for rec, _ in divergences
                                       if rec.get("decisive", True))
    skipped = collections.Counter(rec["class"] for rec in declined)
    shown = [c for c in ALL_CLASSES if totals[c]]

    if args.json:
        json.dump({
            "level": 2, "repos": len(answered), "repos_in_corpus": len(cases),
            "checked": sum(totals.values()),
            "declining_cases": ["%s [%s]" % (r, v) for r, v in declining],
            "by_class": {c: {"cases": totals[c], "divergences": bad[c], "declined": skipped[c],
                             "decisive": decisive[c], "decisive_divergences": bad_decisive[c]}
                         for c in shown},
            "divergences": [dict(rec, adapter=got) for rec, got in divergences],
        }, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return

    if declining:
        print("%d of %d cases were answered with null everywhere: the adapter declines them.\n"
              "That is a valid answer -- level 1 is a different question, and a walker that only\n"
              "ever reports files cannot be asked about a directory.\n"
              % (len(declining), len(cases)))
        if not divergences and not declined:
            return

    print("level 2: %d of %d repositories answered, %d queries, oracle git.\n"
          % (len(answered), len(cases), sum(totals.values())))
    print("  %-18s %8s %8s %8s %9s %8s" %
          ("class", "cases", "wrong", "declined", "decisive", "wrong"))
    for cls in shown:
        rate = 100.0 * bad[cls] / totals[cls]
        print("  %-18s %8d %8d %8d %9d %8d   %5.1f%% wrong" %
              (cls, totals[cls], bad[cls], skipped[cls], decisive[cls], bad_decisive[cls], rate))
    print("  %-18s %8d %8d %8d %9d %8d" %
          ("all", sum(totals.values()), len(divergences), len(declined),
           sum(decisive.values()), sum(bad_decisive.values())))
    if sum(decisive.values()) != sum(totals.values()):
        print("\n  the two right-hand columns are the ones that count: a query is decisive only\n"
              "  if its verdict moves when the field under test is removed. The rest keep git's\n"
              "  answer either way, so getting them right proves nothing about the field.")

    if not divergences:
        print("\nno divergences: every answered query was git's answer.")
        return

    print("\n%d divergence%s:\n" % (len(divergences), "" if len(divergences) == 1 else "s"))
    for record, got in divergences[:args.show]:
        print("  %s" % record["path"])
        print("      git says %s, adapter says %s" %
              ("ignored" if record["ignored"] else "not ignored",
               "ignored" if got else "not ignored"))
        print("      class:     %s (rule file in %r)" %
              (record["class"], record.get("owner", "")))
        print("      pattern:   %s" % (record.get("git_pattern") or
                                       "(none -- git found nothing that matched)"))
        print("      from:      %s\n" % record["repo"])
    if len(divergences) > args.show:
        print("  ... and %d more (--show N, or --json for all of it)\n"
              % (len(divergences) - args.show))

    guilty = collections.Counter(rec.get("from_pattern") or "(no matching pattern)"
                                 for rec, _ in divergences)
    print("  most common guilty patterns:")
    for pattern, count in guilty.most_common(8):
        print("    %4d  %s" % (count, pattern))


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
    ap.add_argument("--corpus", help="default: corpus/cases.json, or cases_l2.json with --level 2")
    ap.add_argument("--level", type=int, choices=(1, 2), default=1,
                    help="1: one .gitignore's patterns. 2: the whole tree of rule files.")
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

    corpus = load_corpus(args.corpus or (DEFAULT_CORPUS_L2 if args.level == 2
                                         else DEFAULT_CORPUS))
    if corpus.get("level", 1) != args.level:
        die("that corpus is level %s and you asked for level %d." %
            (corpus.get("level", 1), args.level))

    if args.level == 2:
        # A level-2 case is a whole repository, and the kind lives in each query inside it, not
        # in the case -- so `c["kind"]` doesn't exist here and the level-1 filter silently
        # matched nothing to exclude. It accepted --kind and ignored it, which is worse than
        # refusing: two runs with opposite flags printed the same 4,373 queries. The corpus
        # ships each repository once per variant, so the honest mapping is kind -> variant. Note
        # `inside` answers to `--kind file`: its queries are files, the directory above them is
        # scenery. Mapping it to `dir` because it materialises directories would hide 4,490 file
        # questions behind a flag that promises files.
        variant = {"file": ("files", "inside"), "dir": ("dirs",)}.get(args.kind)
        cases = [c for c in corpus["cases"]
                 if (not args.repo or c["repo"] == args.repo)
                 and (not variant or c.get("variant", "files") in variant)]
        if args.limit:
            cases = cases[:args.limit]
        if not cases:
            die("that selection matches no repositories.")
        adapter = Adapter(argv)
        try:
            divergences, declined, declining = run_l2(cases, adapter)
        finally:
            adapter.close()
        report_l2(divergences, declined, declining, cases, args)
        return 1 if divergences else 0

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
