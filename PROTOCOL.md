# The adapter protocol

One page. If your language can read a line from stdin and write a line to stdout, it can be
measured here — the bench never imports your library, it talks to a process.

There are two levels, and they are two different questions asked of two different kinds of code.
Say which one you implement; nobody is graded on a promise they never made.

| level | the question | who can answer it |
|---|---|---|
| **1** | given *one* set of `.gitignore` lines, which paths are ignored? | a pattern-matching library — `pathspec`, `gitignore_parser`, `node-ignore` |
| **2** | given a *tree* of rule files, which paths are ignored? | whatever walks the repo — the tool, not the library |

Level 1 is **frozen**. The request shape below will not change; new corpus cases can be added, the
protocol cannot. If you implemented it, you stay implemented.

## Level 1 — one file's worth of patterns

## Shape

`gic.py` starts your adapter **once** and speaks newline-delimited JSON over stdin/stdout.

**Request** — one JSON object on one line:

```json
{"id": 0, "patterns": ["*.log", "!keep.log", "build/"], "queries": ["a.log", "build/", "src/x.c"]}
```

* `patterns` — the lines of one `.gitignore`, verbatim and in order. Comments, blank lines and
  trailing whitespace are included exactly as the file had them. Do not pre-filter them for me;
  how you handle a `#` or a stray `\r` is part of what is being measured.
* `queries` — repo-relative paths. Always `/`-separated, never absolute, never starting with `./`.
  **A trailing `/` means the path is a directory.** Nothing else marks directory-ness: there is no
  filesystem here, and there does not need to be.

**Response** — one JSON object on one line, flushed:

```json
{"id": 0, "ignored": [true, false, false]}
```

* `id` must echo the request's `id`. Requests arrive in order and each gets exactly one reply.
* `ignored` must have the same length as `queries` and be in the same order.
* Each element is `true`, `false`, or `null`. **`null` means "my library cannot answer this"** —
  it is reported separately and is not counted as a divergence. Say `null` rather than guessing;
  an honest gap is more useful to your users than a coin flip that happens to land right.

When stdin closes, exit.

## The whole thing, in Python

```python
import json, sys, pathspec
for line in sys.stdin:
    req = json.loads(line)
    spec = pathspec.GitIgnoreSpec.from_lines(req["patterns"])
    out = [bool(spec.match_file(q)) for q in req["queries"]]
    sys.stdout.write(json.dumps({"id": req["id"], "ignored": out}) + "\n")
    sys.stdout.flush()
```

That is a real adapter, not a sketch — it is `adapters/pathspec_adapter.py` with the comments
taken out.

## Things that will bite you, and did bite me

* **Flush.** Buffered stdout deadlocks: the bench is waiting for your line and you are waiting for
  more input. Every adapter in `adapters/` flushes explicitly.
* **A trailing slash changes the answer, and it is supposed to.** `sub` and `sub/` are different
  queries. git itself answers them differently — with `.gitignore` of `.devcontainer/*`,
  `git check-ignore .devcontainer/` matches and `git check-ignore .devcontainer` does not. If your
  library has no way to say "this is a directory", answer `null` for directory queries rather than
  stripping the slash.
* **Write to stderr, not stdout.** Anything you print on stdout that is not a reply line will be
  read as a malformed reply, and the bench will tell you so and stop.
* **One process, many requests.** Build your matcher per request; the patterns change every time.
  Forty-three requests is the whole corpus, so per-request setup cost is not worth optimising.

## Level 2 — the tree, which is where the tools live

Level 1 hands you the lines of one file. Real repositories do not have one file: they have a
`.gitignore` per directory, each one scoped to its own subtree, plus `.git/info/exclude` sitting
underneath the lot. Deciding which of those applies to a given path is **not** something
`pathspec`, `gitignore_parser` or `node-ignore` claim to do — none of them takes a directory. It is
done by the caller, in a loop, usually in about eight lines, and that is precisely why it is worth
measuring separately: the eight lines are nobody's job to test.

A level-2 request carries `level`, `rules` and (optionally) `exclude` instead of `patterns`:

```json
{"id": 0, "level": 2,
 "rules": {"": ["*.log", "build/"], "src": ["!*.log"], "docs/api": ["*.md"]},
 "exclude": ["*.tmp"],
 "queries": ["a.log", "src/b.log", "docs/api/x.md", "src/c.tmp"]}
```

* `rules` — one entry per rule file that exists in the tree. The key is the **directory that
  contains it**, repo-relative, `/`-separated, no trailing slash; the root is the empty string `""`.
  The value is that file's lines, verbatim and in order, exactly as in level 1.
* `exclude` — the lines of `.git/info/exclude`, or absent if the repo has none. It behaves as a
  rule file at the root and it sits **below** the root `.gitignore` in precedence.
* `queries` and the reply are unchanged from level 1, trailing slash and all.

Three things that are easier to learn here than from a wrong answer:

* **One request per repository, not per path.** A level-2 request hands you a whole tree and asks
  about every interesting path in it at once — a hundred-odd queries is normal. Build the tree
  once per request; do not rebuild it per query.
* **`rules` values are lists of lines**, same as level 1's `patterns` — not one newline-joined
  string. Until the 74th session the bench forwarded the corpus's stored string here, so a
  faithful reading of this page (`for line in rules[dir]`) iterated 878 characters instead of 40
  lines. That was the bench's bug and it is fixed; the shape above is what goes on the wire.
* **`exclude` is exercised by one corpus only, and it ships apart.** The 66 cases of
  `cases_l2.json` all lack a `.git/info/exclude`; a clean scorecard there says nothing about
  yours. `cases_l2_exclude.json` is the file that asks — 33 repositories, 99 queries, of which
  **33 are decisive** (their verdict moves when the field is removed; the other 66 are order
  controls that only bite once you read the file at all). Its denominator is not the other
  corpus's, which is exactly why it is a separate file and a separate number.

`variant` appears in the corpus file next to each case; it is bookkeeping for `--kind` (each
repository ships twice, once with its paths materialised as files and once as directories) and it
is never sent to an adapter. If you are looking for it in a request, stop.

The rules of the game, which are git's and not mine:

1. A rule file only speaks about its own directory and below. `src/.gitignore` cannot say anything
   about `docs/`.
2. Deeper wins. Within one file, the **last** matching line wins. Between files, the one in the
   **deeper** directory wins outright — a `!*.log` in `src/.gitignore` re-includes `src/b.log` even
   though the root said `*.log`.
3. And the trap underneath all of it: *"It is not possible to re-include a file if a parent
   directory of that file is excluded"*. If the root ignores `build/`, no `!` anywhere below it
   brings `build/x.c` back, because git never walks in to read the rule.

**Not in level 2, and said out loud so nobody discovers it by surprise:** `core.excludesFile` and
the per-user global ignore (they are machine state, not repository state), `.gitattributes`, the
index — a tracked file is never "ignored" no matter what any of these say, and the bench only ever
asks about untracked paths.

**If you only do level 1, say so by declining.** Answer a level-2 request with `null` for every
query. That is reported as *declined*, not as a divergence, and it is the honest answer: your
library was asked something it never offered to do.
