# The adapter protocol

One page. If your language can read a line from stdin and write a line to stdout, it can be
measured here — the bench never imports your library, it talks to a process.

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
