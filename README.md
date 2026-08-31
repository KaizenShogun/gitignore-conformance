# gitignore-conformance

Three popular libraries say they implement `.gitignore`. Point this at them and git says otherwise:

```
pathspec 1.1.1 (GitIgnoreSpec)   9852 checks    69 divergences
gitignore_parser 0.1.13          9852 checks    76 divergences
node-ignore 7.0.6                9852 checks     1 divergence
```

Point it at the *tools* that walk a tree of `.gitignore` files instead, and the spread is wider —
one flat list of rules versus one dict per directory, 48 wrong out of 179 versus 0 out of 433.
That's [level 2](#level-2-the-tree-of-rule-files-which-is-nobodys-library), below.

This is a differential conformance bench. It carries a frozen corpus of 9,852 questions built from
the root `.gitignore` of 43 real repositories — cpython, kubernetes, rust, next.js, pytorch — where
every answer is git's own, taken from a real file in a real working tree. You write a twelve-line
adapter in your language, it prints the paths where you disagree with git.

Not a conformance percentage. A percentage tells you how you feel; a path tells you what to fix:

```
$ python3 gic.py -- python3 adapters/pathspec_adapter.py
69 divergences from git in 9852 cases:

  .idea/eclipseCodeFormatter.xml
      git says ignored, adapter says not ignored
      pattern:   .idea/
      from:      elastic/elasticsearch

  Debug/.clang-format
      git says ignored, adapter says not ignored
      pattern:   Debug/
      from:      nodejs/node

  ... and 49 more (--show N to see them, --json for all of it)

  most common guilty patterns:
       6  .idea/
       5  (no matching pattern)
       5  Debug/
       5  Release/
       5  /tools/msvs/genfiles/
```

## Why this exists

Andrew Nesbitt put it exactly right in *The Many Flavors of Ignore Files* (12 Feb 2026):

> Tools using Go's `filepath.Match` get different behavior from tools using the `ignore` npm
> package, which get different behavior from tools using Python's `pathspec` library, which get
> different behavior from tools calling out to git's own matching code.

and:

> A formal spec with a shared test suite could let tool authors say "we implement level 1"… rather
> than the current vague gesture at gitignore compatibility.

I can't write the spec. I can write the shared test suite, and this is it. Right now GitHub has
81 repositories whose description is some form of "gitignore parser", three of the most-starred of
which advertise themselves as *spec-compliant*, and none of them ships anything that could check
that claim. Saying "spec-compliant" should cost more than a line in a README.

## Quickstart

No network, no git, no Python needed to *use* the corpus — it's a JSON file. To run the bench you
need Python 3.8+ and nothing else; no dependencies, ever.

```sh
git clone https://github.com/KaizenShogun/gitignore-conformance
cd gitignore-conformance
python3 gic.py -- python3 adapters/pathspec_adapter.py     # exit 0 if you agree with git, 1 if not
python3 gic.py --kind dir -- node adapters/node_ignore_adapter.js
python3 gic.py --repo python/cpython --limit 20 -- ./my-adapter
```

The adapter protocol is one page: [`PROTOCOL.md`](PROTOCOL.md). Your process reads a line of JSON
from stdin (`patterns` + `queries`) and writes a line of JSON to stdout (`ignored`). That's the
whole contract, so any language qualifies — the bench never imports your library, it talks to a
process. Answering `null` means "my library can't express this question"; those are counted and
reported separately, never as divergences. An honest gap is more useful to your users than a coin
flip that happens to land right.

## The three implementations I could run here

Measured 23 Aug 2026 against the full corpus. Every row is 9,852 checks and `declined: 0` — nobody
ducked a single question.

| implementation | version | divergences | where they cluster |
|---|---|---:|---|
| `pathspec` (`GitIgnoreSpec`, Python) | 1.1.1 | **69** | `nodejs/node` 42, `langchain-ai/langchain` 16 |
| `gitignore_parser` (Python) | 0.1.13 | **76** | `prometheus/prometheus` 27, `langchain-ai/langchain` 16, `rust-lang/rust` 12 |
| `node-ignore` (JS, out of the box) | 7.0.6 | **1** | `python/cpython` |
| `node-ignore` with `ignorecase: false` | 7.0.6 | **0** | — |

Read it fairly, because the headline is unkind and the detail matters:

* **node-ignore's single divergence is a documented default, not a bug.** With cpython's
  `.gitignore`, the pattern `/python` matches the file `Python` — because `ignorecase` is `true` by
  default. Turn it off and the number is zero. Across all 43 repositories that default costs
  exactly one path, and cpython defends itself by hand against it with a `!/Python/` line and a
  comment explaining why. Worth knowing if you're a consumer of the library; not worth a bug report.
* **The Python numbers are real divergences, and they are not one exotic pattern each.** The
  biggest clusters are ordinary directory patterns — `.idea/`, `Debug/`, `Release/`,
  `/tools/msvs/genfiles/` — where git ignores a file inside the directory and the library says it
  doesn't. That's not a corner of the spec anybody would call obscure. I haven't finished
  diagnosing every one of them and I'm not going to pretend otherwise; the bench's job is to hand
  the maintainer the path, the pattern and the repo, and it does. Separately, I did chase one
  `pathspec` bug to the bottom and send it upstream —
  [cpburnz/python-pathspec#133](https://github.com/cpburnz/python-pathspec/pull/133), where `*` and
  `**` compile to a regex that never captures the directory marker, so no directory-only negation
  can re-include anything.
* **Divergence is not fault.** These libraries are not toys and I've read their code; two of them
  are load-bearing for tools I use. The point of a bench is that "compatible with git" becomes a
  number you can watch, not an adjective.

Reproduce any row in one command. If you maintain one of these and think a case is wrong, the
corpus tells you which repository, which pattern and which path — argue with that, not with me.

## Level 2: the tree of rule files, which is nobody's library

Level 1 asks a pattern library about one `.gitignore`. Level 2 asks about the *tree* — a
`.gitignore` in `docs/` and another in `vendor/`, each scoped to its own subtree, plus
`.git/info/exclude` on top. `pathspec`, `gitignore_parser` and `node-ignore` don't promise that
layer and shouldn't: it belongs to whoever walks the directories. It's eight lines of glue in a
tool nobody reviews, and it's exactly the "layering, anchoring and directory semantics" Nesbitt
says trip people up.

So level 2 measures a different subject: not a library, a **tool**. Same protocol, one request per
repository — you get the whole tree of rule files at once and answer every query. 33 repositories,
each asked twice: once about paths that are **files** (2,224 queries) and once about the
**directories** on the way to them (2,239). 4,463 questions, git 2.55.0, `corpus/excluded_l2.json`
empty again.

The two halves are not decoration. A walker prunes directories; a rule that wrongly kills `docs/`
never gets the chance to be wrong about `docs/api.md`, so a file-only bench measures the survivors
of the mistake and not the mistake. Seven of the nine divergences below are directory queries.

```sh
python3 gic.py --level 2 -- python3 adapters/black_adapter.py
```

Every question carries the class of what it's testing, because a single conformance number would
hide the whole finding — a tool can be perfect on its own directory and wrong one level over:

| class | the query asks | of the file queries, git ignores |
|---|---|---|
| `own` | a rule matching a file beside its own rule file | 76.7% |
| `sibling` | that same rule leaking into a **sibling** subtree, where it must not apply | 20.2% |
| `root` | a root rule reaching down into a subdirectory | 16.5% |
| `deeper` | a rule reaching further down its own subtree | 58.0% |
| `from_root` | an anchored root rule, `/build`-style | 57.1% |

(The directory half runs hotter — `build/` matches the directory and not the file, so `own` is 95.8%
ignored there against 76.7% here. Compare rates across tools, not across the two halves.)

### Two tools, and the difference is a data structure

| tool | version | answered | `sibling` wrong | wrong overall |
|---|---|---:|---:|---:|
| [`simonw/files-to-prompt`](https://github.com/simonw/files-to-prompt) | `main`, fetched 29 Aug 2026 | 2,224 of 4,463 | **48 / 179** paired | 257 / 2,224 |
| [`psf/black`](https://github.com/psf/black) | 26.5.1 (PyPI wheel) | 4,373 of 4,463 | 0 / 870 | 9 / 4,373 |

Neither denominator is the corpus, and they are not each other's. `files-to-prompt` prints a list of
files, so it cannot answer a question about a directory: it declines all 33 directory cases in one
block, which is a legitimate answer and never a denominator. `black` answers both halves and
declines one repository — its own, on both variants, because a `.gitignore` shipped in its test data
crashes the walk (below).

Where they overlap, the difference is a data structure. `files-to-prompt` keeps `gitignore_rules` as
a single flat list and does `gitignore_rules.extend(...)` on entering each directory, never trimming
on the way out (`cli.py:122-128`). So a rule written in `a/.gitignore` stays live in the sibling
`b/`. `black` passes a `dict[Path, GitIgnoreSpec]` keyed by directory and rebuilds it per child
(`files.py`, `gen_python_files`), and scores 0 out of 870 on the class that kills the flat list.
**The residue is the structure, not the author** — that's the only reading of these two rows I'll
defend.

black's nine, by class and by half:

| class | file queries | directory queries | total |
|---|---:|---:|---:|
| `own` | 2 / 444 | 3 / 444 | 5 / 888 |
| `deeper` | 0 / 481 | 3 / 483 | 3 / 964 |
| `root` | 0 / 394 | 1 / 402 | 1 / 796 |
| `sibling` | 0 / 433 | 0 / 437 | 0 / 870 |
| `from_root` | 0 / 427 | 0 / 428 | 0 / 855 |
| **all** | **2 / 2,179** | **7 / 2,194** | **9 / 4,373** |

Read the numbers carefully, because the raw ones lie in both directions:

* **The `sibling` column for `files-to-prompt` is a *paired* number, not the raw one.** Its level-1
  matching is separately broken (1,044 / 7,942 = 13.1% on the level-1 corpus: `should_ignore` runs
  `fnmatch` on the *basename* against the whole rule, so `/build` and `docs/_build/` never match
  anything). A raw per-class table would just be measuring that. So the bench groups the answers by
  *the same leaf seen from four places* and conditions on "the `own` side is right": 248 such seams,
  179 with `own` correct, and **48 of those 179 get the sibling wrong**. The matching error cancels;
  what's left is the tree.
* **The control is what makes it a finding.** On seams where git ignores from both sides — where the
  tree can't discriminate — `files-to-prompt` is **0 wrong out of 79**. It only fails where scoping
  is the thing being tested.
* **Its `root` 0% is not a merit, it's the same defect with the sign flipped.** The control there is
  11 wrong out of 59: a flat list that never trims is *always* going to say yes to a root rule.
* **26.8% is a floor, and it depends on `scandir` order.** All 179 seams are explained by walk order:
  sibling visited *before* the owner → 4 wrong of 131; sibling visited *after* → **39 of 39 wrong**;
  sibling pruned → **5 of 5**. The 73% that came out right came out right by luck of directory
  iteration order. I predicted >70% failure before running it and was wrong; the mechanism, not my
  guess, is what the number means.
* **Eight of `black`'s nine are one line, and it is black's line, not `pathspec`'s.** Eight are
  negations that never take effect: `!packages/playground/.vscode` in react, `!/node_modules` in
  vscode, `!.idea/` and `!/.idea/inspectionProfiles` in cpython, `!volumes/functions/deno.json*` in
  supabase. `_path_is_ignored` (`files.py:292-309`) walks the `gitignore_dict` from least to most
  specific and `return True`s on the first spec that matches — an OR with a short circuit, so the
  deepest file, the only one that re-includes, is never consulted. black **ignores too much**, and
  silently: `black --check -v .` on the react fixture prunes a file git tracks and exits 0. Reported
  as [psf/black#5376](https://github.com/psf/black/issues/5376) with a CLI repro and a control.
  Attribution is not a guess — a three-legged check against git, against
  `gen_python_files` with black's own defaults, and against my adapter; and each pattern was replayed
  through `pathspec` alone, which answers as git does when it is handed the rule file that should
  decide.
* **The ninth is the library's, and it goes the other way.** `/node_modules` in nodejs/node: git
  ignores, black doesn't. `pathspec`'s `SimpleGiBackend.match_file` answers differently forward and
  in reverse, and the reverse — the direction black uses — is the one that diverges from git. Filed
  upstream as [cpburnz/python-pathspec#134](https://github.com/cpburnz/python-pathspec/issues/134).
  So the nine are fully attributed: 8 to the caller, 1 to the library, 0 to my harness.
* **One crash, not swallowed.** A `.gitignore` whose only line is `!` — git accepts it; it ships in
  black's own test data — makes `get_gitignore` raise `GitIgnorePatternError` and abort the entire
  walk. The adapter declines that repository out loud on stderr rather than reporting "not ignored",
  which is why black answers 64 of the 66 cases and 4,373 of the 4,463 questions. A declined
  repository is never a denominator.

The black rows became two bug reports because they came with a path, a pattern and a repro. The
`files-to-prompt` row hasn't: it's a tool I use, the number is the point, and the maintainer can
decide whether a flat list is a bug or a documented approximation. Ten minutes with `--level 2` on
your own walker is cheaper than finding out from a user who shipped a file they meant to ignore.

## What's real and what's derived — read this before quoting a number

The corpus is honest about its own construction, in public, because a bench that hides its
generator is asking you to trust a stranger's reading of a spec. That is the whole thing I'm
trying to replace.

**Real:**

* the 43 `.gitignore` files, fetched verbatim from the default branch of each repository, with URL
  and sha256 in [`corpus/PROVENANCE.md`](corpus/PROVENANCE.md);
* the working tree — every path was created as an actual file or directory inside an actual
  `git init` repository;
* the verdict — **git 2.55.0**, asked three separate ways: `git check-ignore`, `git add --dry-run`
  and `git status --ignored`. A case only enters the corpus if all three agree. Disagreements are
  dropped, never guessed, and counted in `corpus/excluded.json`. **The count is zero**: the 15%
  ceiling I wrote down before looking never came close to mattering.

**Derived:** the paths themselves. From a repository's own patterns I concretise one path per
pattern (`*.log` → `sample.log`) plus its near neighbours (`sample.log.keepme`, `keepsample.log`,
`vendor/sample.log`) — because a corpus of only-matching paths is 95% "ignored", and an
implementation that answers `true` to everything would score 95%. The corpus as frozen is 63%
ignored / 37% not, with 1,910 directory questions, and there's a test that fails if that balance
drifts.

The generator refuses to invent where it would be putting *my* reading of the spec into a corpus
that is supposed to contain git's: character classes (`[abc]`) are skipped rather than expanded,
and literal backslashes are dropped. That second rule exists because an earlier round of this
generator invented paths with literal backslashes that no repository on earth has, and I nearly
reported them as bugs to someone.

So: the *questions* are synthetic, the *answers* are git's. If you find a question that isn't worth
asking, open an issue — that's a real defect in the corpus and I'd like to know.

`build_corpus.py` regenerates the whole thing (that one does need network and git). Using it
doesn't.

## Prior art, and where this is thin

* **[`svent/gitignore-test`](https://github.com/svent/gitignore-test)** is the closest thing that
  exists, and it's 6 stars, 2 KB, created and last pushed the same day in December 2015, no
  licence. A hand-written fixture from one afternoon, ten and a half years ago. Different animal:
  synthetic patterns rather than what large projects actually write, and no adapter protocol.
* **git's own [`t0008-ignores.sh`](https://github.com/git/git/blob/master/t/t0008-ignores.sh) and
  the wildmatch tests** are the real prior art and are far more rigorous than this about pattern
  matching. They're also written in git's test harness and aimed at git. Nesbitt names the gap I'm
  filling: *"they only cover pattern matching, not the layering, anchoring, and directory semantics
  that trip up most implementations."* If you only implement globbing, go there first.
* **Structurally** this is [`http-garden`](https://github.com/narfindustries/http-garden) and
  [`h2spec`](https://github.com/summerwind/h2spec) for a much smaller protocol: differential
  testing against reference implementations, one adapter per subject.

Thin spots, stated rather than discovered by you later: the **level-1** corpus is one root
`.gitignore` per repository, so nested layering isn't in it at all — that's what
`corpus/cases_l2.json` and the section above exist for, and it's a separate corpus with a separate
generator (`build_corpus_l2.py`), built the same way and just as frozen. Level 2 has its own gap:
every query is a **file** path, so tools that decide about a *directory* before recursing into it
are only measured through the files underneath. Adding directory queries is the next thing. And the four
rows in the table above come from a run on 23 Aug 2026 whose JSON output I still have; the machine
I'm writing this on no longer has those three libraries installed, so `tests.sh` skips the adapter
integration test and reports `not installed here, skipped`. My own 27 tests pass without them.

## Tests

```sh
bash tests.sh      # 27 tests, OK (skipped=1), about a second
```

MIT. Built by Midas.
