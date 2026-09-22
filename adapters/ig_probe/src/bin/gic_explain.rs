// Same subject as `gic_serve`, one extra bit of the answer: *which* of the crate's three verdicts
// came back, and which glob produced it. `gic_serve` collapses Whitelist and None into `0`, which
// is what a conformance bench wants and exactly what you cannot classify a divergence with.
//
// Line protocol, same shape as `gic_serve`:
//     CASE            start a case
//     R<line>         one .gitignore line, verbatim after the R
//     Q<path>         one query; a trailing slash means directory
//     GO              answer: one line per query, then a `.`
//
// One answer line per query, tab-separated:
//     <parents verdict>\t<parents glob>\t<matched verdict>\t<matched glob>
// verdict is I (ignore), W (whitelist) or N (none); glob is the pattern's `original()`, or `-`.
use ignore::gitignore::GitignoreBuilder;
use ignore::Match;
use std::io::{self, BufRead, Write};

fn describe(m: &Match<&ignore::gitignore::Glob>) -> (char, String) {
    match m {
        Match::None => ('N', "-".to_string()),
        Match::Ignore(g) => ('I', g.original().to_string()),
        Match::Whitelist(g) => ('W', g.original().to_string()),
    }
}

fn main() {
    let stdin = io::stdin();
    let mut out = io::stdout();
    let mut rules: Vec<String> = Vec::new();
    let mut queries: Vec<String> = Vec::new();

    for line in stdin.lock().lines() {
        let line = line.unwrap();
        if line == "CASE" {
            rules.clear();
            queries.clear();
        } else if let Some(r) = line.strip_prefix('R') {
            rules.push(r.to_string());
        } else if let Some(q) = line.strip_prefix('Q') {
            queries.push(q.to_string());
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
                let is_dir = query.ends_with('/');
                let bare = query.trim_end_matches('/');
                let (pv, pg) = describe(&gi.matched_path_or_any_parents(bare, is_dir));
                let (mv, mg) = describe(&gi.matched(bare, is_dir));
                buf.push_str(&format!("{}\t{}\t{}\t{}\n", pv, pg, mv, mg));
            }
            buf.push_str(".\n");
            out.write_all(buf.as_bytes()).unwrap();
            out.flush().unwrap();
        }
    }
}
