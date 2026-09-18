// A bench server for the standalone *matcher* of the `ignore` crate (the one ripgrep, fd and ruff
// are built on).
//
// The `rg` binary the bench already measures never asks this question: its walk descends level by
// level and prunes excluded directories, so the matcher is never handed a path underneath a pruned
// one. The subject here is the public API the crate's own doc-comment offers "when you have a list
// of paths without hierarchy" — `matched_path_or_any_parents` — and its sibling `matched`.
//
// Line protocol (spoken by `adapters/ig_matcher_adapter.py`), plain text because I don't want serde
// anywhere near the subject:
//     CASE            start a case
//     R<line>         one .gitignore line, verbatim after the R
//     Q<path>         one query; a trailing slash means directory
//     GO              answer: one `0`/`1` line per query, then a `.`
// If the rules fail to compile, the answer is a single `E` line and the adapter declines the case.
use ignore::gitignore::GitignoreBuilder;
use std::io::{self, BufRead, Write};

fn main() {
    // `--api=matched` measures the other door of the same library; the ancestors one is default.
    let parents = !std::env::args().any(|a| a == "--api=matched");

    let stdin = io::stdin();
    let mut out = io::stdout();
    let mut rules: Vec<String> = Vec::new();
    let mut queries: Vec<String> = Vec::new();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line == "CASE" {
            rules.clear();
            queries.clear();
        } else if let Some(rest) = line.strip_prefix('R') {
            rules.push(rest.to_string());
        } else if let Some(rest) = line.strip_prefix('Q') {
            queries.push(rest.to_string());
        } else if line == "GO" {
            let mut builder = GitignoreBuilder::new("");
            let mut broke = false;
            for rule in &rules {
                if builder.add_line(None, rule).is_err() {
                    broke = true;
                }
            }
            let gi = match builder.build() {
                Ok(gi) if !broke => gi,
                _ => {
                    writeln!(out, "E").unwrap();
                    out.flush().unwrap();
                    continue;
                }
            };
            let mut buf = String::new();
            for query in &queries {
                // The API wants the path WITHOUT a trailing slash and `is_dir` as a separate flag.
                // That is the shape of question the crate promises, and the shape that changes the
                // answer: handing it `__tmp/` instead of (`__tmp`, true) is a different question.
                let is_dir = query.ends_with('/');
                let bare = query.trim_end_matches('/');
                let m = if parents {
                    gi.matched_path_or_any_parents(bare, is_dir)
                } else {
                    gi.matched(bare, is_dir)
                };
                buf.push(if m.is_ignore() { '1' } else { '0' });
                buf.push('\n');
            }
            buf.push_str(".\n");
            out.write_all(buf.as_bytes()).unwrap();
            out.flush().unwrap();
        }
    }
}
