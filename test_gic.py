#!/usr/bin/env python3
"""Tests for the bench itself.

A conformance bench that is wrong is worse than no bench: it convicts working libraries and,
worse, acquits broken ones. So most of what is checked here is the bench's own failure modes --
that a divergence is actually reported, that a broken adapter is told apart from a failing one,
and that the frozen corpus still says what it says.

    python3 test_gic.py            # everything that does not need the network
    python3 -m unittest -v test_gic

Tests needing `git` skip themselves without it. Nothing here needs the network.
"""
import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_corpus
import gic

CORPUS = os.path.join(HERE, "corpus", "cases.json")
HAVE_GIT = shutil.which("git") is not None


def write_adapter(directory, body):
    """A one-off adapter, written to disk. Returns the command that runs it."""
    path = os.path.join(directory, "adapter_%d.py" % abs(hash(body)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import json, sys\n" + textwrap.dedent(body))
    return [sys.executable, path]


class TinyCorpus:
    """A four-case corpus written to a temp file, so tests never depend on the frozen one."""

    def __init__(self, directory):
        self.corpus = {
            "version": 1,
            "oracle": "git (hand-written fixture)",
            "note": "fixture",
            "gitignores": {
                "fixture/whitelist": {"url": "", "sha256": "",
                                      "patterns": ["*", "!*/", "!*.py"]},
            },
            "cases": [
                {"repo": "fixture/whitelist", "path": "sub/", "kind": "dir",
                 "ignored": False, "pattern": "!*/"},
                {"repo": "fixture/whitelist", "path": "sub/a.py", "kind": "file",
                 "ignored": False, "pattern": "!*.py"},
                {"repo": "fixture/whitelist", "path": "sub/b.txt", "kind": "file",
                 "ignored": True, "pattern": "*"},
                {"repo": "fixture/whitelist", "path": "top.txt", "kind": "file",
                 "ignored": True, "pattern": "*"},
            ],
        }
        self.path = os.path.join(directory, "tiny.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.corpus, fh)


def run_gic(corpus_path, adapter_argv, extra=()):
    cmd = [sys.executable, os.path.join(HERE, "gic.py"), "--corpus", corpus_path]
    cmd += list(extra) + ["--"] + list(adapter_argv)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE, timeout=600)
    return proc


# --------------------------------------------------------------------- the bench's own wiring

class BenchWiring(unittest.TestCase):
    """Does gic.py report what happened, and does it tell "wrong" apart from "broken"?"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gic-test-")
        self.tiny = TinyCorpus(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def adapter(self, body):
        return write_adapter(self.tmp, body)

    def test_agreeing_adapter_exits_zero(self):
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                truth = {"sub/": False, "sub/a.py": False, "sub/b.txt": True, "top.txt": True}
                sys.stdout.write(json.dumps(
                    {"id": req["id"], "ignored": [truth[q] for q in req["queries"]]}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no divergences", proc.stdout)

    def test_one_wrong_answer_is_found_and_named(self):
        """The whole point: a divergence must come out with its path, pattern and repo."""
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                out = [True if q == "sub/" else
                       {"sub/a.py": False, "sub/b.txt": True, "top.txt": True}[q]
                       for q in req["queries"]]
                sys.stdout.write(json.dumps({"id": req["id"], "ignored": out}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("1 divergence from git", proc.stdout)
        self.assertIn("sub/", proc.stdout)
        self.assertIn("!*/", proc.stdout)                  # the guilty pattern
        self.assertIn("fixture/whitelist", proc.stdout)    # where it came from

    def test_null_is_declined_not_a_failure(self):
        """An honest "I can't answer that" must not be laundered into a pass or a fail."""
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                truth = {"sub/": False, "sub/a.py": False, "sub/b.txt": True, "top.txt": True}
                out = [None if q.endswith("/") else truth[q] for q in req["queries"]]
                sys.stdout.write(json.dumps({"id": req["id"], "ignored": out}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("declined", proc.stdout)
        self.assertIn("no divergences", proc.stdout)

    def test_wrong_answer_count_is_a_broken_adapter_not_a_failing_one(self):
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                sys.stdout.write(json.dumps({"id": req["id"], "ignored": [True]}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("line up", proc.stderr)

    def test_out_of_order_reply_is_caught(self):
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                sys.stdout.write(json.dumps(
                    {"id": req["id"] + 99, "ignored": [False] * len(req["queries"])}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("one reply per request", proc.stderr)

    def test_garbage_on_stdout_is_caught(self):
        argv = self.adapter("""
            for line in sys.stdin:
                sys.stdout.write("Traceback (most recent call last):\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not JSON", proc.stderr)

    def test_adapter_that_dies_is_caught(self):
        argv = self.adapter("""
            sys.exit(1)
        """)
        proc = run_gic(self.tiny.path, argv)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("closed stdout", proc.stderr)

    def test_missing_adapter_binary(self):
        proc = run_gic(self.tiny.path, ["./no-such-adapter-anywhere"])
        self.assertEqual(proc.returncode, 2)

    def test_missing_corpus(self):
        proc = run_gic(os.path.join(self.tmp, "nope.json"), [sys.executable, "-c", "pass"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no corpus", proc.stderr)

    def test_json_report_is_machine_readable(self):
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                sys.stdout.write(json.dumps(
                    {"id": req["id"], "ignored": [True] * len(req["queries"])}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv, extra=["--json"])
        report = json.loads(proc.stdout)
        self.assertEqual(report["checked"], 4)
        self.assertEqual(len(report["divergences"]), 2)          # the two not-ignored cases
        for entry in report["divergences"]:
            self.assertEqual(set(entry), {"repo", "path", "kind", "pattern", "git", "adapter"})

    def test_selection_flags(self):
        argv = self.adapter("""
            for line in sys.stdin:
                req = json.loads(line)
                sys.stdout.write(json.dumps(
                    {"id": req["id"], "ignored": [True] * len(req["queries"])}) + "\\n")
                sys.stdout.flush()
        """)
        proc = run_gic(self.tiny.path, argv, extra=["--json", "--kind", "dir"])
        self.assertEqual(json.loads(proc.stdout)["checked"], 1)
        proc = run_gic(self.tiny.path, argv, extra=["--json", "--limit", "2"])
        self.assertEqual(json.loads(proc.stdout)["checked"], 2)


# --------------------------------------------------------------------- the control

class BenchActuallyDetects(unittest.TestCase):
    """The control from the notebook: a bench that finds nothing might be measuring nothing.

    `pathspec` 1.1.1 has a divergence I found and reported upstream before writing any of this
    (cpburnz/python-pathspec#133): with the whitelist idiom `*` / `!*/` / `!*.py`, git leaves
    `sub/` un-ignored so it can descend and re-include the `.py` files, and `GitIgnoreSpec` says
    the directory is ignored. If the bench cannot see that, the bench is broken -- so it is
    checked here rather than assumed.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gic-control-")
        self.tiny = TinyCorpus(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_known_pathspec_divergence_is_visible(self):
        try:
            import pathspec  # noqa: F401
        except ImportError:
            self.skipTest("pathspec not installed")
        proc = run_gic(self.tiny.path, [sys.executable,
                                        os.path.join(HERE, "adapters", "pathspec_adapter.py")],
                       extra=["--json"])
        self.assertEqual(proc.returncode, 1, "the bench did not see a divergence it must see")
        report = json.loads(proc.stdout)
        paths = [d["path"] for d in report["divergences"]]
        self.assertIn("sub/", paths)

    @unittest.skipUnless(HAVE_GIT, "needs git")
    def test_the_fixture_matches_what_git_says_today(self):
        """And the fixture's own answers are git's, re-measured, not remembered."""
        repo = os.path.join(self.tmp, "r")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("*\n!*/\n!*.py\n")
        for path in ("sub/a.py", "sub/b.txt", "top.txt"):
            full = os.path.join(repo, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        verdict = build_corpus.check_ignore(repo, ["sub", "sub/a.py", "sub/b.txt", "top.txt"])
        self.assertFalse(verdict["sub"][1], "git changed its mind about the whitelist idiom")
        self.assertFalse(verdict["sub/a.py"][1])
        self.assertTrue(verdict["sub/b.txt"][1])
        self.assertTrue(verdict["top.txt"][1])


# --------------------------------------------------------------------- the corpus builder

class Candidates(unittest.TestCase):

    def test_split_patterns(self):
        dirs, files, negs = build_corpus.split_patterns(
            "# comment\n\nbuild/\n*.log\n!keep.log\n  \n/anchored\n")
        self.assertEqual(dirs, ["build/"])
        self.assertEqual(files, ["*.log", "/anchored"])
        self.assertEqual(negs, ["keep.log"])

    def test_concretize_refuses_to_guess(self):
        """Character classes and backslashes are git's business, not my generator's."""
        self.assertIsNone(build_corpus.concretize("*.[oa]"))
        self.assertIsNone(build_corpus.concretize("foo\\#bar"))
        self.assertIsNone(build_corpus.concretize("/"))
        self.assertIsNone(build_corpus.concretize("../escape"))
        self.assertEqual(build_corpus.concretize("*.log"), "sample.log")
        self.assertEqual(build_corpus.concretize("build/"), "build")
        self.assertEqual(build_corpus.concretize("**/node_modules"), "node_modules")

    def test_near_misses_are_neighbours_not_matches(self):
        misses = build_corpus.near_misses("build")
        self.assertIn("build.keepme", misses)
        self.assertIn("keepbuild", misses)
        self.assertIn("vendor/build", misses)

    def test_candidates_cover_more_than_the_obvious(self):
        paths = build_corpus.candidate_paths("build/\n*.log\n!keep.log\n")
        self.assertTrue(any(p.startswith("build/") for p in paths))
        self.assertTrue(any(p.startswith("pkg/") for p in paths))     # unanchored reach
        self.assertIn("keep.log", paths)                              # negations asked about
        self.assertEqual(len(paths), len(set(paths)))

    @unittest.skipUnless(HAVE_GIT, "needs git")
    def test_check_ignore_does_not_read_the_exit_code_as_the_verdict(self):
        """`check-ignore` exiting 0 means "a pattern decided", not "ignored"."""
        repo = tempfile.mkdtemp(prefix="gic-ci-")
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("*.log\n!keep.log\n")
        out = build_corpus.check_ignore(repo, ["a.log", "keep.log", "plain.txt"])
        self.assertEqual(out["a.log"][:2], (True, True))
        self.assertEqual(out["keep.log"][:2], (True, False))    # decided, by a negation
        self.assertEqual(out["plain.txt"][:2], (False, False))  # nothing had anything to say

    @unittest.skipUnless(HAVE_GIT, "needs git")
    def test_the_oracle_never_overwrites_the_gitignore_it_is_measuring(self):
        """The bug that cost 58 poisoned cases: `.gitignore` is itself a candidate path."""
        work = tempfile.mkdtemp(prefix="gic-oracle-")
        self.addCleanup(shutil.rmtree, work, True)
        cases, bad = build_corpus.oracle("*.log\n!.gitignore\n", [".gitignore", "a.log"], work)
        answers = {c["path"]: c["ignored"] for c in cases}
        self.assertEqual(answers.get("a.log"), True,
                         "the rules were lost while materialising paths")
        self.assertEqual(bad, [])


# --------------------------------------------------------------------- the frozen corpus

class FrozenCorpus(unittest.TestCase):
    """These run offline against the JSON. No git, no network, no library."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CORPUS):
            raise unittest.SkipTest("no corpus/cases.json -- run build_corpus.py")
        with open(CORPUS, encoding="utf-8") as fh:
            cls.corpus = json.load(fh)

    def test_shape(self):
        self.assertGreater(len(self.corpus["cases"]), 5000)
        self.assertGreaterEqual(len(self.corpus["gitignores"]), 40)
        self.assertTrue(self.corpus["oracle"].startswith("git "))

    def test_every_case_carries_its_provenance(self):
        for case in self.corpus["cases"]:
            self.assertIn(case["repo"], self.corpus["gitignores"], case["path"])
            self.assertIn(case["kind"], ("file", "dir"))
            self.assertIsInstance(case["ignored"], bool)

    def test_directory_cases_are_marked_by_a_trailing_slash_and_files_are_not(self):
        """The protocol has no filesystem; the slash is the only thing that says "directory"."""
        for case in self.corpus["cases"]:
            if case["kind"] == "dir":
                self.assertTrue(case["path"].endswith("/"), case["path"])
            else:
                self.assertFalse(case["path"].endswith("/"), case["path"])
            self.assertFalse(case["path"].startswith("/"), case["path"])
            self.assertFalse(case["path"].startswith("./"), case["path"])

    def test_no_duplicate_questions(self):
        seen = set()
        for case in self.corpus["cases"]:
            key = (case["repo"], case["path"])
            self.assertNotIn(key, seen, "asked twice: %s" % (key,))
            seen.add(key)

    def test_an_ignored_case_is_never_blamed_on_a_negation(self):
        """If git says "ignored" and names a `!` pattern, one of the two is being misread."""
        for case in self.corpus["cases"]:
            if case["ignored"] and case["pattern"]:
                self.assertFalse(case["pattern"].startswith("!"), case)

    def test_both_answers_are_well_represented(self):
        """A corpus of 95% "ignored" is passed by an adapter that always says yes."""
        ignored = sum(1 for c in self.corpus["cases"] if c["ignored"])
        share = ignored / len(self.corpus["cases"])
        self.assertGreater(share, 0.25)
        self.assertLess(share, 0.75)

    def test_the_patterns_travel_with_the_corpus_and_match_their_hash(self):
        """Offline reproducibility: the sha256 recorded is of the text these lines came from."""
        for repo, meta in self.corpus["gitignores"].items():
            text = "\n".join(meta["patterns"])
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), meta["sha256"],
                             "%s: the stored patterns are not the file that was hashed" % repo)

    def test_the_corpus_is_json_and_only_json(self):
        """Criterion: usable with no network, no git and no Python. Guard it in a test."""
        with open(CORPUS, "rb") as fh:
            json.loads(fh.read().decode("utf-8"))


class FrozenCorpusL2(unittest.TestCase):
    """The level-2 corpus, offline. Its own class because its shape is not level 1's."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(HERE, "corpus", "cases_l2.json")
        if not os.path.exists(path):
            raise unittest.SkipTest("no corpus/cases_l2.json -- run build_oracle_l2.py")
        with open(path, encoding="utf-8") as fh:
            cls.corpus = json.load(fh)

    def test_a_repo_is_asked_about_directories_too(self):
        """The hole this closes: until the 67th session every query was a file, so no prune
        could ever be caught -- and pruning a directory is the one mistake that is wrong about
        everything below it at once."""
        variants = collections.Counter(c.get("variant") for c in self.corpus["cases"])
        self.assertGreater(variants["dirs"], 20, "no directory cases in the corpus")
        self.assertGreater(variants["files"], 20)

    def test_rule_files_reach_the_adapter_as_lines_like_level_1(self):
        """PROTOCOL.md documents `rules` values as lists of lines, and level 1 has always sent
        `patterns` that way. The corpus stores them newline-joined, and `ask_l2` used to forward
        the string: an adapter written from the page did `for line in ...` over a str and got
        one pattern per character. Both my adapters accepted either shape, so nothing failed.

        This pins the wire, not the storage -- and the round-trip, since adapters re-join."""
        sent = {}

        class Spy(gic.Adapter):
            def __init__(self):
                pass

            def _roundtrip(self, request_id, request, queries):
                sent.update(request)
                return [None] * len(queries)

        case = self.corpus["cases"][0]
        Spy().ask_l2(0, case["rules"], "*.tmp\n!keep.tmp", case["queries"])

        self.assertEqual(sent["level"], 2)
        for directory, value in sent["rules"].items():
            self.assertIsInstance(value, list, "rules[%r] went out as %s, not a list of lines"
                                  % (directory, type(value).__name__))
            for line in value:
                self.assertNotIn("\n", line, "a 'line' still contains a newline")
        self.assertEqual(sent["exclude"], ["*.tmp", "!keep.tmp"])

        # Joining back must reproduce the file byte for byte, or the measurement moved.
        for directory, value in sent["rules"].items():
            self.assertEqual("\n".join(value), case["rules"][directory])

    def test_the_slash_is_the_only_thing_that_says_directory(self):
        for case in self.corpus["cases"]:
            want_dir = case["variant"] == "dirs"
            for query, meta in zip(case["queries"], case["meta"]):
                self.assertEqual(query.endswith("/"), want_dir, query)
                self.assertEqual(meta["kind"], "dir" if want_dir else "file", query)
                self.assertFalse(query.startswith(("/", "./")), query)

    def test_kind_selects_the_variant_instead_of_being_swallowed(self):
        """A level-2 case is a repository and the kind lives in its queries, so the level-1
        `c["kind"]` filter matched nothing and --kind was accepted and ignored: two runs with
        opposite flags printed the same 4,373 queries. A flag that decides in silence is worse
        than one that refuses."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        argv = write_adapter(tmp, """
            for line in sys.stdin:
                req = json.loads(line)
                sys.stdout.write(json.dumps(
                    {"id": req["id"], "ignored": [True] * len(req["queries"])}) + "\\n")
                sys.stdout.flush()
        """)
        path = os.path.join(HERE, "corpus", "cases_l2.json")
        seen = {}
        # `inside` answers to --kind file: its queries are files. Leaving it out of this tuple is
        # how the flag would quietly stop covering 4,490 of the file questions.
        for kind, variants in (("file", ("files", "inside")), ("dir", ("dirs",))):
            proc = run_gic(path, argv, extra=["--level", "2", "--json",
                                              "--kind", kind, "--limit", "2"])
            report = json.loads(proc.stdout)
            want = [c for c in self.corpus["cases"] if c["variant"] in variants][:2]
            self.assertEqual(report["checked"], sum(len(c["meta"]) for c in want), kind)
            self.assertEqual(report["repos"], 2, kind)
            # An always-True adapter is wrong about everything git does not ignore, so the
            # divergence list is a free sample of what was actually asked -- and every path in
            # it must have the shape of the half we selected.
            self.assertTrue(report["divergences"], "nothing was asked, the check is vacuous")
            for record in report["divergences"]:
                self.assertEqual(record["kind"], kind, record["path"])
                self.assertEqual(record["path"].endswith("/"), kind == "dir", record["path"])
            seen[kind] = [r["path"] for r in report["divergences"]]
        self.assertNotEqual(seen["file"], seen["dir"])

    def test_the_three_variants_ask_the_same_tree(self):
        """Same rules, same names; files, directories, and files under those directories. If the
        trees drifted apart the numbers would not be comparable, which is the whole point."""
        by_repo = collections.defaultdict(dict)
        for case in self.corpus["cases"]:
            by_repo[case["repo"]][case["variant"]] = case
        paired = 0
        for repo, group in by_repo.items():
            if len(group) != 3:
                continue
            paired += 1
            self.assertEqual(group["files"]["rules"], group["dirs"]["rules"], repo)
            self.assertEqual(group["files"]["rules"], group["inside"]["rules"], repo)
        self.assertGreater(paired, 20)

    def test_inside_queries_are_inherited_and_nothing_else(self):
        """The point of `inside` is that its leaf name cannot match anything, so the verdict comes
        from the ancestor. A query whose leaf were a real name would be a `deeper` in disguise."""
        n = 0
        for case in self.corpus["cases"]:
            if case["variant"] != "inside":
                continue
            for query, meta in zip(case["queries"], case["meta"]):
                n += 1
                self.assertFalse(query.endswith("/"), query)
                self.assertEqual(meta["kind"], "file", query)
                self.assertIn(meta["class"], ("inside", "inside_deep"), query)
                self.assertTrue(query.startswith(meta["ancestor"] + "/"), query)
                self.assertEqual(os.path.basename(query), "_gic_keep", query)
        self.assertGreater(n, 1000)

    def test_the_class_table_covers_every_class_the_corpus_carries(self):
        """The per-class table is filtered through a hardcoded list, so a class the corpus grew
        after that list was written disappears from the report while the *total* stays right --
        the output goes on looking complete. That is exactly how `inside` and `inside_deep`, more
        than half the corpus, sat out of every `by_class` for a day."""
        carried = {meta["class"] for case in self.corpus["cases"] for meta in case["meta"]}
        self.assertTrue(carried - set(gic.CLASSES), "the corpus should carry more than the five")
        missing = carried - set(gic.ALL_CLASSES)
        self.assertEqual(missing, set(), "classes the report would silently drop: %s" % missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
