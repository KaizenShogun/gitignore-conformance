# gitignore-conformance

Three popular libraries say they implement `.gitignore`. Point this at them and git says otherwise:

```
pathspec 1.1.1 (GitIgnoreSpec)   9852 checks    69 divergences
gitignore_parser 0.1.13          9852 checks    76 divergences
node-ignore 7.0.6                9852 checks     1 divergence
```

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

Thin spots, stated rather than discovered by you later: the corpus is **one root `.gitignore` per
repository**, so nested `.gitignore` layering and `.git/info/exclude` are barely exercised — that's
the first thing I'd add, and it's the exact semantics Nesbitt says trip people up. And the four
rows in the table above come from a run on 23 Aug 2026 whose JSON output I still have; the machine
I'm writing this on no longer has those three libraries installed, so `tests.sh` skips the adapter
integration test and reports `not installed here, skipped`. My own 27 tests pass without them.

## Tests

```sh
bash tests.sh      # 27 tests, OK (skipped=1), about a second
```

MIT. Built by Midas.
