# gitignore-conformance

Three popular libraries say they implement `.gitignore`. Point this at them and git says otherwise:

```
pathspec 1.1.1 (GitIgnoreSpec)   9852 checks    69 divergences
gitignore_parser 0.1.13          9852 checks    76 divergences
node-ignore 7.0.6                9852 checks     1 divergence
```

Point it at the *tools* that walk a tree of `.gitignore` files instead, and the spread is wider —
one flat list of rules versus one dict per directory, 48 wrong out of 179 versus 0 out of 433. The
one project that promises the layer instead of improvising it gets 1 wrong out of 4,441. That's
[level 2](#level-2-the-tree-of-rule-files-which-is-nobodys-library), below.

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
each asked three times over the same rule files: about paths that are **files** (2,224 queries),
about the **directories** on the way to them (2,239), and about files **inside** those directories
(4,490). 8,953 questions, git 2.55.0, `corpus/excluded_l2.json` empty again.

The three are not decoration. A walker prunes directories; a rule that wrongly kills `docs/` never
gets the chance to be wrong about `docs/api.md`, so a file-only bench measures the survivors of the
mistake and not the mistake. Seven of the nine divergences below are directory queries.

And the third variant is here because the first two weren't enough, which is worth more than a
clean story. `inside` asks about `D/name/_gic_keep` and `D/name/_gic_deep/_gic_keep`: leaf names
that cannot match any pattern, so the verdict is inherited from the ancestor and from nothing else.
It was added on 10 Sep 2026 after `git-pkgs/gitignore` matched `mypkg.egg-info/` and not
`mypkg.egg-info/PKG-INFO` while all 4,463 questions stayed green. **Every number below with a
denominator of 4,463 predates it** — those measurements are still true of the questions they asked,
and the subject-by-subject columns have not been re-run on the full 8,953 yet. The one subject that
has been: `git-pkgs/gitignore` went from 2 divergences to 24 when the variant landed, of which 12
are the `*.egg-info/` bug and 12 are two further ones ([#25](https://github.com/git-pkgs/gitignore/issues/25),
[#26](https://github.com/git-pkgs/gitignore/issues/26)) that no earlier query could see.

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
| `inside` | a file directly under a queried directory — nothing in its own name matches | 62.3% |
| `inside_deep` | the same, one level further down | 62.3% |

(The directory half runs hotter — `build/` matches the directory and not the file, so `own` is 95.8%
ignored there against 76.7% here. Compare rates across tools, not across the three variants.)

`inside` and `inside_deep` score identically to the query, which is not a bug in either: git
prunes an ignored directory, so its whole subtree inherits one verdict and the oracle cannot tell
the two depths apart. A **subject** can — one that inherits for a direct child and re-decides from
scratch deeper down splits them — which is the only reason both are asked.

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

### The one project that promises the layer: `dvc`

Everything above measures tools that improvise the tree layer because no library sells it. So the
obvious question is whether it can be done right at all, and the answer needed a subject that
*promises* it. [`iterative/dvc`](https://github.com/treeverse/dvc) does — `.dvcignore`, one per
directory, documented as gitignore syntax — and unlike the three level-2 implementations I found by
code search (1, 0 and 3 stars between them), it has 15,862 stars and users who would notice.

| tool | version | answered | wrong overall |
|---|---|---:|---:|
| [`dvc`](https://github.com/treeverse/dvc) | 3.67.1 (PyPI), `pathspec` 1.1.1 | 4,441 of 4,463 | **1 / 4,441** |

| class | queries | wrong |
|---|---:|---:|
| `own` | 898 | 1 |
| `deeper` | 974 | 0 |
| `from_root` | 885 | 0 |
| `sibling` | 880 | 0 |
| `root` | 804 | 0 |

It runs on the same `pathspec` 1.1.1 as the three recipes above, which is what makes the comparison
worth anything: the library is held still and only the caller's code changes. `dvc/ignore.py` keeps
a `pygtrie` of directory → merged pattern list, rewrites a child file's patterns onto the parent's
prefix, scans matches in reverse so the last one wins, and walks a path's ancestors so an excluded
directory can't be re-included from below. That is the whole shape of the problem, written down by
someone who had to ship it. 71.925 % for the flat recipe, 99.977 % for this.

**The one divergence.** `supabase/supabase`'s `docker/.gitignore` — the same two lines that break
recipe R2 above:

```
volumes/functions/**
!volumes/functions/deno.json*
```

git re-includes `deno.jsonsample`; dvc does not. `DvcIgnorePatterns._ignore` walks the ancestor
prefixes and breaks on the first match, and `_find_matching_pattern` probes a directory as
`path + "/"` — so `volumes/functions/**`, which `pathspec` compiles to `^volumes/functions/`,
claims the bare directory, the loop stops there and the negation is never reached. The probe can't
just be dropped: a real `X/` pattern compiles to `^X(?P<ps_d>/)` and needs it. Reported as
[treeverse/dvc#11095](https://github.com/treeverse/dvc/issues/11095) with a `dvc check-ignore`
repro and two controls — plain negation still works, and without the `**` git refuses to re-include
too, which is what pins the variable on `**` rather than on the `!`.

**Two cases are declined, and the reason is mine.** `cpburnz/python-pathspec`'s tree has `.*`
followed by `!.gitignore` at the root. My adapter writes the corpus's rule files out as
`.dvcignore`, so after the rename `dev/.dvcignore` falls under `.*` with nothing re-including it —
and dvc, unlike git, does not read a rule file that its ancestors ignore. `dev`'s two patterns
silently never applied, and that was 8 of the 9 divergences I was one commit away from blaming on
dvc. The adapter now *detects* it rather than guessing: every case is answered a second time with
`!.dvcignore` appended at the root, and a case is declined only if its verdicts actually move. The
probe tree is a detector; the reported answer always comes from the corpus's rules verbatim. Forced
through with the re-include in place, dvc gets all 22 of those queries right.

That difference is real on its own terms, and it is not the rename that causes it — with `.*` and
no re-include at all, a pattern that covers `.gitignore` and `.dvcignore` equally, git applies the
nested rule file and dvc skips it. I have not filed it, because `_update_trie` skips an ignored
rule file in an explicit branch: that is a decision somebody made, not a slip, and a corpus cannot
tell me their intent. It is worth knowing that one `.*` in a root `.dvcignore` silently disables
every nested one.

### The other project that promises the layer: `ripgrep` — which is also `fd`, and nearly `ruff`

dvc was one subject. One subject can't tell you whether doing the tree layer right is normal or
exceptional, so here is a second, found the same way — by looking for who *promises* the layer
rather than for who writes it badly. (Code search on the query pattern was pure noise: 24 of 30
hits were vendored `pathspec` inside a `venv`.) [`BurntSushi/ripgrep`](https://github.com/BurntSushi/ripgrep)
promises it about as hard as anyone: nested `.gitignore` with correct scoping and precedence is a
headline feature, not an incidental.

You can't ask a walker "is this ignored?", so the adapter asks what it would walk, over two
cross-checked channels — `--files` for the paths it would search, `--debug` for the `ignoring ./x:`
lines, which cover directories that `--files` can never mention. When the channels disagree the
adapter answers `null` and says so on stderr rather than picking the one it likes; that is what
caught a `lstrip("./")` in my own code turning `.next` into `next`, in one run, before it could be
scored as ripgrep's fault.

| tool | version | answered | wrong overall |
|---|---|---:|---:|
| [`ripgrep`](https://github.com/BurntSushi/ripgrep) | 14.1.1 (musl release binary) | 4,463 of 4,463 | **5 / 4,463** |
| [`fd`](https://github.com/sharkdp/fd) | 10.5.0 (musl release binary) | 4,463 of 4,463 | **5 / 4,463** |

Two rows, one number, and that is the point of the second row. fd walks with the same `ignore`
crate, so scoring it separately asks whether the divergences belong to the binary or to the
component underneath. Compared as *sets* rather than counts — because 5 and 5 would look identical
even if they were ten different bugs — the symmetric difference is empty in both directions: the
same five paths, the same query class (`from_root`), the same guilty patterns. Every other class is
0.0% for both. That's a component's signature, not two authors making similar mistakes.

**All five are one mechanism.** They come from `psf/black`'s
`tests/data/invalid_nested_gitignore_tests/a/.gitignore`, which holds a single `!`. git strips the
`!`, finds nothing left to negate, and moves on. The `ignore` crate compiles it to the glob `**/`
marked as a whitelist — which matches everything, so it re-includes the whole subtree, including
paths an ancestor `.gitignore` excluded. `--debug` names it in one line:

```
Gitignore(Glob { from: Some("./a/.gitignore"), original: "!", actual: "**/", is_whitelist: true })
```

Filed as [BurntSushi/ripgrep#3527](https://github.com/BurntSushi/ripgrep/issues/3527). It is not the
old "ripgrep rejects a pattern git accepts" complaint from #373/#646/#945: `**local.properties`, the
exact example in those threads, behaves correctly in 14.1.1. This one isn't rejected, it's compiled
into something that matches everything — and the control that *doesn't* diverge is what separates
the two.

**What it costs, and one caveat I only saw by measuring.** Root `.gitignore` with `.venv/` and
`generated/`, 300 `.py` files in each, the same file's last line `!` versus `#c`:

| | `rg --files` | `fd -tf` | `ruff check` | `git status -uall` |
|---|---:|---:|---:|---:|
| `!` | 600 | 600 | 300 diagnostics | 0 |
| `#c` | 0 | 0 | 0 | 0 |

ruff 0.16.6 is in that table because it walks with `ignore` too, and it lints 300 generated files
its user told git to ignore. But with only `.venv/` in the tree ruff scored 0 in *both* rows — its
own default `exclude` list covers `.venv`, `build`, `dist`, `node_modules`, so it never reaches the
gitignore question at all. Smaller blast radius than fd, for a reason that has nothing to do with
gitignore handling. Writing that down instead of the tidier "all three fail identically" is the
difference between a measurement and a slogan.

**And one correction to my own filing.** I first reported that the whitelist re-includes `.git/`,
using a repro that puts `!` in `a/.gitignore`. A whitelist in `a/` can't reach `.git/`, which sits
above it; what I'd seen was `--hidden` revealing a dotfile. With the `!` in the **root**
`.gitignore` the claim holds and is worse than I said — plain `rg`, no flags, walks `.git/`
(18 entries vs 0 in the control). Right conclusion, wrong repro, corrected in the thread.

### The subject that promises to *be* git: `libgit2`

dvc promises the layer. The `ignore` crate promises it. libgit2 doesn't promise to support
gitignore — it promises to be git, and `git_ignore_path_is_ignored()` is the same question
`git check-ignore` answers with the same documented contract, written again in C by other people.
It is also the load-bearing kind of infrastructure: pygit2, git2-rs, nodegit and a long tail of
things that speak git without shelling out to git all get their answer from that function.

`adapters/libgit2_adapter.py` is the shortest adapter here, and that's the finding in miniature.
There is no eight-line walk to write, no precedence to reimplement, no directory heuristic to
guess: the subject exposes the question directly, so the adapter only builds the tree and asks.
The C side is `adapters/lgignore.c`, sixty lines, with the cmake and `cc` invocations in its header.

Three static builds of the **same** source commit — `0551dfd4`, same flags, same machine, only the
patch differing — because at the time of measuring there were two open pull requests aimed at this
exact machinery:

| build | corpus (4,463 queries) | `.git/info/exclude` corpus (99 queries) |
|---|---:|---:|
| `main` @ `0551dfd4` | **6** | **33** |
| `main` + [#7339](https://github.com/libgit2/libgit2/pull/7339) | **0** | **0** |
| `main` + [#7369](https://github.com/libgit2/libgit2/pull/7369) | 5 | 33 |

The 0 in that table is worth more to me than the 6. It is the same harness, the same adapter, the
same oracle and the same corpus in all three rows, so a build that scores zero is the control that
says the bench isn't manufacturing divergences — the thing I could never prove with a subject that
only ever fails.

**The hole had an owner, and reading the tracker first is what found it.** Both of `main`'s
families were already filed, in June, by the same person, with zero comments on either:
[#7284](https://github.com/libgit2/libgit2/issues/7284) (a nested `!vendor` failing to re-include
what the root's `**/vendor/` excluded) and
[#7283](https://github.com/libgit2/libgit2/issues/7283) (`!d/sub/*` wrongly re-including under an
excluded `d/`). Eleven searches of that tracker cost half a minute; opening a third issue would
have cost the maintainers' patience.

So the useful work wasn't an issue, it was arbitration. #7339 fixes all six and both issues' verbatim
repros, with nothing new. #7369 fixes #7283's two, leaves #7284's four, and **breaks one case `main`
gets right** — which reduces to the same four rule lines split across two files:

```
.gitignore      x/
t/.gitignore    !x/
                /x/*
                !/x/keep
```

git does not ignore `t/x/keep/f.txt`; `main` agrees; with #7369 it comes back ignored. Put the
identical four lines in *one* file and every build is correct. That patch makes a negative match
non-conclusive so the walk continues upward — and upward it finds the root's `x/`, which the deeper
`!x/` had already outranked. Its own new test keeps both lines in one file, which is exactly why the
suite stays green. Both measurements are in the PR threads.

**The `.git/info/exclude` column is a second finding and it reduces to five lines.** `.gitignore`
holding `!a`, `.git/info/exclude` holding `a` — git does not ignore `a`, because `.gitignore`
outranks `info/exclude` and the `!` in the higher-precedence source wins. `main` says ignored. Swap
the sources (`.gitignore: c`, `exclude: !c`) and `main` is right, so it is not an inverted stacking
order; put both lines in one file and it is right there too. The negation was never allowed to leave
its own source. #7339's title — *honor nested negation across rule sources* — names it precisely,
and its 0 above covers this corpus too, which neither its description nor its tests claim.

**What this does not measure.** Only `git_ignore_path_is_ignored()`. #7339 also touches
`iterator.c`, so `git_status` and the workdir iterator are outside these numbers, and I said so in
the thread rather than letting a clean table imply more than it earned.

### The other subject that promises to *be* git: `dulwich`

libgit2 is git rewritten in C. `dulwich` is git rewritten in Python, and its
`IgnoreFilterManager` promises the level-2 layer outright: a `.gitignore` per directory loaded on
demand, `.git/info/exclude` and the user's global excludes stacked underneath, one
`is_ignored(path)` for the whole repository. It is also the cleanest subject here for two reasons
that cost earlier sessions real time — the rule files keep the name `.gitignore` (dvc renames
them, and a renaming transport quietly changes the question), and directory-ness is in the API,
documented with the same trailing slash `git check-ignore` uses.

Same corpus, same oracle. Two builds of the same code plus one deliberately patched:

| subject | divergences (4,463 queries) |
|---|---:|
| 1.2.14 (PyPI wheel) | **28** |
| tip of `main`, `2d728c2a` | **28** |
| the same, with `find_matching`'s filter loop reversed | 9 |

The first two rows are the same 28 cases, not just the same count — `dulwich/ignore.py` is
byte-identical in the wheel and on `main`, so nothing merged since the release touches this.
`porcelain.check_ignore` — what `dulwich check-ignore` runs — gives the same 28 as the API, which
is what makes it a user-visible number rather than an internal-API detail.

**22 of the 28 are one mechanism: between two rule files, the shallower one decides.** git gives
the deeper `.gitignore` precedence; `find_matching` accumulates its filters with
`filters.insert(0, …)`, walks them deepest-first, and `is_ignored` takes the last match — which
therefore comes from the file closest to the root. Reduced from `nodejs/node`, with git re-asked at
every reduction step:

```
.gitignore                                        !deps/v8/**
deps/v8/third_party/ittapi/ittapi-rs/.gitignore   Cargo.lock
```

git ignores `…/ittapi-rs/Cargo.lock`; dulwich does not. Put those exact two lines in one file and
dulwich is right, which is the paired control: ordering *within* a file is correct, ordering
*between* files is reversed. It goes both ways — cpython's root `.idea/` against a nested `!.idea/`
comes back ignored where git re-includes it.

The third row is an instrument, not a proposal. Reversing that one loop fixes 22, leaves 6 and
**breaks 3**, so it is not a fix; it is what attributes the 22 to that line rather than to my
harness. Its 48-test suite passes identically with and without the change, so nothing in the suite
pins the current order either way — and `test_nested_gitignores` covers this exact shape and passes
only because its root negation matches a directory instead of the queried file.

**The 3 it breaks are a second defect the current order was masking:** a bare `!` line. `psf/black`
ships one, in `tests/data/invalid_nested_gitignore_tests/a/.gitignore`, whose only content is `!`.
With `build/` and `!` in a single file, dulwich stops ignoring `build/`; swap the `!` for `#c`,
another line git makes no pattern of, and it agrees again. Same corner the Rust `ignore` crate gets
wrong in its own way ([ripgrep#3527](https://github.com/BurntSushi/ripgrep/issues/3527)).

**The remaining 6 are a third one**, surviving the reordering and reproducing in a single file:
`ollama/ollama`'s root `.vscode` against a nested `!.vscode/extensions.json`. git keeps it ignored
— a file cannot be re-included while a parent directory stays excluded — and dulwich re-includes
it. That is a `_check_parent_exclusion` gap, in the neighbourhood of dulwich's own #2141 but
outside what the test that landed with it covers.

**What this does not measure.** Only `is_ignored` and `porcelain.check_ignore`: not `status`, not
`add`, not the index-aware paths, not `ignorecase`, nothing Windows-specific.

### The third one: `go-git`, where the released code and `main` are two different subjects

go-git is git rewritten in Go, and the one whose answer travels furthest: Gitea, ArgoCD and Flux
all reach through it. It promises the level-2 layer the same way — `gitignore.ReadPatterns(fs,
path)` reads `.git/info/exclude` and then walks the tree for `.gitignore` files, and
`NewMatcher(ps).Match(path, isDir)` answers for any path.

Two things make it worth measuring twice. The released `v5.19.2` and the `main` branch
(`v6.0.0-alpha.5`, commit `c3e96df0`, taken 2026-09-08) are **not the same code**: `main` carries
a port of git's own `wildmatch.c` and a `Scope` type the release has never seen. And go-git has two entry
points that a user can hold — the matcher, and `Worktree.Status()`, which is what the downstream
tools actually call. Same corpus, same oracle, same adapter; the only thing that changes between
the two columns is one import path, v5 → v6:

| entry point | v5.19.2 | `main` (v6 alpha) |
|---|---:|---:|
| `ReadPatterns` + `Matcher.Match` (4,463 queries) | **4** | **2** |
| `.git/info/exclude` corpus (99) | **0** | **0** |
| `Worktree.Status()` (2,224 file queries) | **1** | **1** — *a different one* |

Two open pull requests were measured the same way, by their `merge` ref (what GitHub computes as
main + PR), so the column isolates the patch: [#2318](https://github.com/go-git/go-git/pull/2318)
leaves all 4,463 answers untouched and fixes both of its own repros, which is an endorsement of
safety rather than of impact; [#2311](https://github.com/go-git/go-git/pull/2311) fixes #2112 and
takes `main` from 2 to **4**, because it re-includes descendants of a directory that `!dir/` puts
back. The `Status()` divergence is filed as [#2369](https://github.com/go-git/go-git/issues/2369),
bisected to `70ab8844` — a commit that fixed the mirror-image case, so it traded one broken family
for another rather than causing a plain regression.

Four divergences in 4,463 is the closest any unpatched subject in this bench has come to git, and
the `exclude` column is a clean zero where libgit2's `main` gets 33 wrong. The interesting part is
not the totals, though. It is that on `main` the two entry points **disagree with each other, in
opposite directions**, so which answer you get depends on which door you came through.

**`main` fixes the `**` the release gets wrong.** `grafana/grafana` ignores `testdata/**output/`,
where the `**` is glued to text inside a component — git treats consecutive asterisks there as an
ordinary `*`. v5.19.2 does not ignore `testdata/xoutput/`; `main` does. Paired controls, git
re-asked at each step: `testdata/*output/` is right on both, and so is `testdata/**/output/`, so
it is the glued `**` and not the trailing slash. That is the `wildmatch` port earning its keep.

**Both matchers still re-include a file underneath an excluded directory.** From `ollama/ollama`:

```
.vscode
!.vscode/extensions.json
```

git keeps `.vscode/extensions.json` ignored — once a directory is excluded, git does not look
inside it again, so nothing in there can be re-included. Both matchers say it is not ignored. The
paired control is the same tree with `.vscode/*` instead of `.vscode`, which is the form git
*does* let you re-include through: there, everything agrees. This one has been reported twice, in
[#694](https://github.com/go-git/go-git/issues/694) (2023, `foo/` + `!foo/bar`) and
[#877](https://github.com/go-git/go-git/issues/877) (2023, a nested `!def` under a root `abc`).
Both were closed by the stale bot rather than by a fix, and both still reproduce on yesterday's
`main`.

**And `Status()` on `main` has a divergence of its own that the matcher does not.** From
`supabase/supabase`'s `docker/.gitignore`:

```
volumes/functions/**
!volumes/functions/deno.json*
```

git does not ignore `docker/volumes/functions/deno.jsonsample` — `volumes/functions/**` never
excluded the directory itself, so the negation is allowed to work. `main`'s `Status()` reports it
as ignored anyway. v5's `Status()` gets it right, and so does `main`'s own matcher, which is what
makes it a regression in the newer walk rather than an old bug: delete the `!` line and every
column agrees again. It is the mirror image of the `.vscode` case — the same new machinery that
teaches `Status()` about excluded parents is what stops a legitimate re-include from working.

**What this does not measure.** Only the matcher and `Worktree.Status()` on a worktree with no
commits and no index: not `Add`, not the sparse-checkout paths, not `ignorecase`. Directory
queries are declined under `Status()`, not guessed — `Status` reports files, and inventing a row
for a directory would be my rule, not go-git's.

#### Should go-git keep its matcher or delegate to a library?

That question has been open in #877 since March, and it is the one case here where the bench gets to
answer a design decision instead of filing a bug. A contributor had already written both halves as
two consecutive commits on a fork, which is the lucky part: `6aa9efc` is go-git with its own engine,
`3edf6ec` is the same tree delegating to `git-pkgs/gitignore` v1.1.1. Taking the parent as the
control isolates the engine swap instead of five months of drift.

| build | `Matcher.Match` | `Status()` |
|---|---:|---:|
| `main` (2026-09-09) | 2 | 1 |
| `6aa9efc` — own matcher | 4 | 1 |
| `3edf6ec` — delegating, v1.1.1 | **5** | **4** |
| the same, both library bugs patched | **2** | **1** |

Delegating as it stands trades two failures for three, and the three are one library bug
([git-pkgs/gitignore#22](https://github.com/git-pkgs/gitignore/pull/22)) that also accounts for the
whole `Status()` column. Patch that and a second one found on the way
([#23](https://github.com/git-pkgs/gitignore/issues/23): a dir-only pattern with a wildcard,
`*.egg-info/`, matches the directory but nothing inside it) and delegating lands exactly where
`main` already is. The engine choice turns out to be orthogonal to the bug in the issue where it is
being discussed: the opening repro of #877 passes on all four builds, and the sibling shape from
#694 — `test/` and `!test/keep` in one file — fails on all four.

**#23 was also a hole in this corpus, and it is worth saying out loud.** The bench never asked
about a file *inside* a directory matched by a wildcard dir-only pattern, so all 4,463 queries
stayed green through a bug that hits 9 real rule lines across 8 of the 33 repos — `*.egg-info/`
alone appears in airflow, transformers, langchain, vscode, flask and pytorch. A corpus built from
real rule files still only asks the questions someone thought to ask.

The hole is now the `inside` variant, and closing it paid for itself immediately. On the 8,953
questions, `git-pkgs/gitignore` at the commit that merged #22 answers 24 wrong instead of 2. Twelve
are #23, and the patch in [#24](https://github.com/git-pkgs/gitignore/pull/24) removes exactly those
twelve and breaks none. The other twelve are two bugs nobody had reported: a negation under an
excluded directory taking effect ([#25](https://github.com/git-pkgs/gitignore/issues/25)) and a
negation inherited by the contents of the directory it re-includes
([#26](https://github.com/git-pkgs/gitignore/issues/26)). Both survive deleting the `literalSuffix`
fast-reject outright, which is how I know they are a different cause and not #23 wearing a hat —
the control that says so is a build with the shortcut removed entirely, scoring the same 12.

### One repository where the pruning costs a real file

Everything above is my corpus: I chose the queries, even if git wrote the answers. So here is the
same finding with nothing of mine in it — `nodejs/node`'s `.gitignore`, which says out loud what it
wants:

```
# Only track the shared base devcontainer.json; ignore everything else under .devcontainer
!.devcontainer/
.devcontainer/**
!.devcontainer/base/
!.devcontainer/base/devcontainer.json
```

git agrees with the comment. Put those lines and that file in a tree and `git add -A -n` stages
`.devcontainer/base/devcontainer.json`. It is tracked on purpose.

`files-to-prompt` prints nothing from under `.devcontainer/`. Not because its matcher decided the
file was ignored — asked directly on that tree, its own `should_ignore` returns **False** for
`.devcontainer/base/devcontainer.json` and **True** for `.devcontainer`. The True on the *directory*
prunes the branch before the file is ever reached, so the matcher that would have got it right never
gets asked. That pair is the whole attribution; without it, a tool that fails 13.1% of level-1 cases
could be dropping the file for any reason at all. `black`'s `gen_python_files` prunes the same
directory. It loses nothing here — a `.json` was never in its `include` — but the prune is the same
prune, so this is a property of the layer and not of one author.

The mechanism is that `.devcontainer/**` covers the *contents* of the directory and not the
directory, which `!.devcontainer/` re-includes. git therefore still walks in and honours the last
negation. A caller that asks `match_file(".devcontainer/")` gets `True` and stops.

And here is where this belongs, which is not an upstream tracker: `pathspec` answers correctly when
you ask it the tree question instead of the per-path one. `GitIgnoreSpec.match_tree_files(root)` on
that same tree ignores exactly `.devcontainer/otro/cosa.txt` and lets `devcontainer.json` through.
The library has a sane API for this. The divergence exists only in the eight lines of glue that call
`match_file(dir + "/")` and prune — which is the level-2 thesis restated in one real repository.

How common is it? Of the 43 repositories the harvested `.gitignore` files come from, 5 have the
lexical shape — a `dir/**` rule
with a negation somewhere underneath — and **1** actually loses a tracked file to it. One in
forty-three is not an epidemic. It is also not zero, and what it costs is a file a repository went
out of its way to keep.

### Three recipes over one unchanged library

If the level-2 divergence lives in the caller's eight lines, the cheap way to prove it is to hold
the library still and change only those lines. Same `pathspec` 1.1.1, same 66 cases, same 4,463
queries, same oracle — [`recipes.py`](recipes.py):

| recipe | wrong | conformant | what it is |
|---|---|---|---|
| flat | **1,253** | 71.925 % | concatenate every rule file into one spec, ask it the full path |
| chain | 3 | 99.933 % | one spec per rule file, deepest one that decides wins |
| chain + prune | 2 | 99.955 % | chain, and a path inherits an ignored ancestor directory |

All **33 repositories** fail the flat recipe. It is not a strawman I built to lose: it is what
`from_lines` looks like it wants when you read the docstring, and the ninety seconds of "just put
all the rules in one list" that precede noticing that a rule file has a *position*. What it costs
is anchoring. `/dist` in `adev/shared-docs/pipeline/tutorials/common/.gitignore` means that
directory's own `dist`; flattened, it means the repository root's.

The 1,253 are not one bug counted 1,253 times, so `--breakdown` splits them:

| | over-ignores | under-ignores |
|---|---|---|
| leaked into another branch | 1,072 | 11 |
| no pattern matches at all | — | 138 |
| wrong base, same subtree | 15 | 1 |
| root file, order/precedence | — | 16 |
| **total** | **1,087** (86.8 %) | **166** (13.2 %) |

The direction matters more than the total. Over-ignoring is the failure that drops a file the
repository deliberately kept — the `.devcontainer/base/devcontainer.json` failure, at scale — and
it is 87 % of this. Under-ignoring is the mirror image of the same lost anchor: `/dist` no longer
reaching `devtools/dist`, so nothing matches and the walker hands you a build directory.

The three chain misses are worth naming individually, because three is small enough to attribute
one by one instead of quoting a rate:

* `nodejs/node`, the directory `node_modules/` — **the library**. `!**/node_modules/**` wins
  (`check_file(...).index` says so, I didn't guess), and git does not consider `dir/**` to cover
  `dir` itself. Same shape as the `.devcontainer` case above; it has a sane answer in
  `match_tree_files`.
* `ollama/ollama`, `app/ui/app/.vscode/extensions.json`, two variants — **the recipe**. The
  deepest rule file re-includes it with `!.vscode/extensions.json`, but `app/.gitignore` already
  excluded the `.vscode` *directory*, and git never walks in to read the negation. Adding pruning
  fixes both, which is the whole argument for R2.

And R2's misses are **not** a subset of R1's — I predicted they would be and was wrong. Pruning
buys `ollama` and loses `supabase/supabase`'s `docker/volumes/functions/deno.jsonsample`, a file
git tracks: `volumes/functions/**` doesn't match its own directory, `pathspec` says it does, and
the prune then propagates the library's wrong answer to everything underneath. A prune amplifies
whatever the matcher got wrong about directories. Zero of the four is my harness.

**What this table is not.** It is a comparison of recipes *measured on `pathspec`*, not a claim
about how often each one appears in the wild. I tried to measure that and couldn't: grep.app
returns 429 on all three queries and GitHub's code search needs a token I don't have here, so the
prevalence is unmeasured and I'm not going to estimate it. `files-to-prompt` is in this README as
a consumer that loses the scope on its own — it doesn't use `pathspec` at all, it runs `fnmatch`
over basenames — so the 28 % up there does not describe it, and it is not evidence about it.

### The third rule file: `.git/info/exclude`

Git reads rules from three places, and the tree of `.gitignore` files is only two of them. The
third is `.git/info/exclude` — a root-level rule file that sits just under the root `.gitignore`
in precedence, and that nobody commits, which is exactly why no corpus built from public
repositories contains one. So this one is **synthetic, and it cannot be otherwise**: a real
`.git/info/exclude` never travels with a repository. Everything else in the case is the repo's
own rule tree; the four lines of `exclude` are mine, and `build_oracle_l2.py exclude` writes them.

It ships as its own corpus, `corpus/cases_l2_exclude.json`, because its denominator is not the
other one's and mixing them is how a number becomes unreadable:

```
gic.py --level 2 --corpus corpus/cases_l2_exclude.json -- <your adapter>
```

33 repositories, 99 queries, three per repo — and only **33 of them decide anything**:

| class | queries | git ignores | **decisive** | what it catches |
|---|---|---|---|---|
| `exclude_only` | 33 | 33 | **33** | named in `exclude` and nowhere else: miss the file, fail |
| `exclude_overridden` | 33 | 0 | **0** | `!` in the root `.gitignore` beats `exclude` |
| `exclude_order` | 33 | 33 | **0** | `!` in `exclude` loses to the root `.gitignore` |

The generator builds every case twice, with and without the `exclude`, and marks a query
**decisive** only if git's verdict actually moves. Two of the three classes don't move: they are
order controls, and against a subject that never opens the file they measure nothing at all. They
stay in the corpus — they bite the moment you *do* read it and stack it in the wrong order — but
they are not scored. Reporting "2 of 3 correct" here would have been smoke.

Against the two subjects above, the decisive column is a shutout:

| subject | decisive queries | wrong | how it fails |
|---|---|---|---|
| `files-to-prompt` | 33 | **33** | *(no matching pattern)* |
| `psf/black` 26.5.1 | 32 | **32** | *(no matching pattern)*, declines the `!`-only repo |

That is "not implemented", not "implemented wrong", and I checked it a third way rather than
inferring it from a scorecard: `grep -rn 'info/exclude\|excludesFile'` over both checkouts returns
nothing, and `files-to-prompt`'s `cli.py` opens `os.path.join(path, ".gitignore")` and no other
file. **This number does not join the nine.** The nine are `.gitignore` divergences; this is a
rule file neither tool ever opens, and adding them would be the same sin as folding directory
queries into a file-only denominator. It gets its own line: *`.git/info/exclude` — 33 decisive
queries, 0 implemented by either subject.*

Whether that's a bug depends on what the tool claims. A walker that says "respects your
`.gitignore`" is telling the truth. One that says "respects your ignore rules" is not.

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
  ceiling I wrote down before looking never came close to mattering. The `exclude` corpus is held
  to the same rule and passes it twice — its 99 queries and the 99 no-`exclude` controls it is
  compared against, zero disagreements (`n_excluded` in the file itself).

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
