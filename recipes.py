"""Three recipes for the level-2 walk, over one unchanged library.

Level 2 — combining the `.gitignore` files of every ancestor directory — is not something any
matcher promises. The caller writes it, usually in about eight lines, and those eight lines are
where the divergence lives. This script holds `pathspec` constant and changes only the recipe, so
anything that moves is a property of the caller's code and not of the matcher (paired contrast).

  R0 "flat"        concatenate every rule file into one spec, ask it the full path.
  R1 "chain"       one spec per rule file; the deepest ancestor that decides wins. No pruning.
  R2 "chain+prune" R1, plus: if an ancestor directory comes out ignored, the path inherits it.
                   That is what a walker does when it refuses to descend.

The oracle is the corpus's `ignored` field, written by `git check-ignore` 2.55.0.

    python3 recipes.py [--corpus corpus/cases_l2.json] [--src DIR] [--breakdown]

`--breakdown` adds the direction of the damage and the family of the winning pattern, because
"1,253 failures" is not a finding: over-ignoring drops a file somebody meant to keep, and
under-ignoring walks into `node_modules/`. They are different bugs with one count.
"""
import argparse
import json
import sys

DROPPED = []


def usable(lines):
    """Lines `pathspec` accepts. A real `.gitignore` carries a bare `!`, where the library raises
    and git shrugs. Dropped ONE AT A TIME and counted: a silent filter turns "I couldn't read it"
    into "not ignored", which is a verdict I never measured."""
    out = []
    for ln in lines:
        try:
            pathspec.GitIgnoreSpec.from_lines([ln])
        except Exception as e:
            DROPPED.append((ln, type(e).__name__))
            continue
        out.append(ln)
    return out


def spec(lines):
    return pathspec.GitIgnoreSpec.from_lines(usable(lines))


def specs_of(rules):
    return {d: spec(v.splitlines()) for d, v in rules.items()}


def ancestors(path):
    """Directories on the path, deepest first, ending at the root (`''`)."""
    parts = path.split("/")[:-1]
    out = []
    while parts:
        out.append("/".join(parts))
        parts.pop()
    out.append("")
    return out


def decide(specs, path, is_dir):
    """Deepest ancestor with a rule file that says anything; if none does, not ignored."""
    q = path + "/" if is_dir else path
    for d in ancestors(path):
        sp = specs.get(d)
        if sp is None:
            continue
        rel = q[len(d) + 1:] if d else q
        r = sp.check_file(rel)
        if r.include is not None:
            return r.include
    return False


def r1(specs, path, is_dir):
    return decide(specs, path, is_dir)


def r2(specs, path, is_dir):
    parts = path.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        if decide(specs, "/".join(parts[:i]), True):
            return True
    return decide(specs, path, is_dir)


def r0(flat, path, is_dir):
    return bool(flat.match_file(path + "/" if is_dir else path))


def flat_with_origins(rules):
    """Flat spec plus the rule file each pattern came from, index-aligned.

    `check_file(...).index` points into the spec's pattern list, so the two lists have to line up
    exactly — `from_lines` drops empty strings, which shifts everything after them. If they don't
    line up this stops instead of printing a wrong attribution.
    """
    pairs = []
    for d in sorted(rules, key=lambda x: x.count("/") if x else -1):
        for ln in rules[d].splitlines():
            pairs.append((ln, d))
    kept = []
    for ln, d in pairs:
        try:
            pathspec.GitIgnoreSpec.from_lines([ln])
        except Exception:
            continue
        if not ln:
            continue
        kept.append((ln, d))
    sp = pathspec.GitIgnoreSpec.from_lines([ln for ln, _ in kept])
    if len(sp.patterns) != len(kept):
        sys.exit(f"STOP: {len(sp.patterns)} patterns vs {len(kept)} lines; index unusable")
    return sp, kept


def family(query, origin, pattern):
    if pattern is None:
        # nothing matches: the anchored rule no longer reaches its own subtree
        return "no pattern matches"
    if origin == "":
        return "root file (order/precedence)"
    under = query == origin or query.startswith(origin + "/")
    return "wrong base (same subtree)" if under else "leaked into another branch"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="corpus/cases_l2.json")
    ap.add_argument("--src", help="directory to import pathspec from (a vendored wheel)")
    ap.add_argument("--breakdown", action="store_true")
    a = ap.parse_args()
    if a.src:
        sys.path.insert(0, a.src)
    global pathspec
    import pathspec as _ps
    pathspec = _ps
    print(f"pathspec {pathspec.__version__} from {pathspec.__file__}", file=sys.stderr)

    cases = json.load(open(a.corpus))["cases"]
    tot = 0
    bad = {"R0": 0, "R1": 0, "R2": 0}
    repos_failing_flat = set()
    direction, fam, samples = {}, {}, {}
    chain_misses = {"R1": [], "R2": []}

    for c in cases:
        specs = specs_of(c["rules"])
        flat, kept = flat_with_origins(c["rules"])
        is_dir = c["variant"] == "dirs"
        for i, q in enumerate(c["queries"]):
            exp = bool(c["ignored"][i])
            tot += 1
            got = {"R0": r0(flat, q, is_dir), "R1": r1(specs, q, is_dir), "R2": r2(specs, q, is_dir)}
            for k, v in got.items():
                if v != exp:
                    bad[k] += 1
                    if k == "R0":
                        repos_failing_flat.add(c["repo"])
                    else:
                        chain_misses[k].append((c["repo"], c["variant"], q, exp))
            if a.breakdown and got["R0"] != exp:
                d = "over-ignores" if got["R0"] else "under-ignores"
                direction[d] = direction.get(d, 0) + 1
                res = flat.check_file(q + "/" if is_dir else q)
                line, origin = kept[res.index] if res.index is not None else (None, "")
                f = family(q, origin, line)
                fam[f] = fam.get(f, 0) + 1
                samples.setdefault((f, d), [])
                if len(samples[(f, d)]) < 2:
                    samples[(f, d)].append((c["repo"], q, origin or "<root>", line))

    print(f"\n{tot} queries, {len(cases)} cases")
    print(f"lines pathspec refused: {len(DROPPED)} -> {sorted(set(DROPPED))[:4]}")
    for k, name in (("R0", "flat"), ("R1", "chain"), ("R2", "chain+prune")):
        print(f"  {k} {name:12s} {bad[k]:5d} wrong   {100 * (tot - bad[k]) / tot:.3f} % conformant")
    print(f"  repositories where the flat recipe fails at least once: {len(repos_failing_flat)}")

    if a.breakdown:
        print("\ndirection of the damage (flat):")
        for k, v in sorted(direction.items(), key=lambda x: -x[1]):
            print(f"  {k:14s} {v:5d}  {100 * v / sum(direction.values()):.1f} %")
        print("\nfamily of the winning pattern (flat):")
        for k, v in sorted(fam.items(), key=lambda x: -x[1]):
            print(f"  {k:30s} {v:5d}  {100 * v / sum(fam.values()):.1f} %")
        for (f, d), rows in sorted(samples.items()):
            print(f"\n  {f} / {d}")
            for repo, q, origin, line in rows:
                print(f"    {repo}  {q}\n      rule {line!r} from {origin}/.gitignore")

    for k in ("R1", "R2"):
        print(f"\n{k} misses ({len(chain_misses[k])}):")
        for row in chain_misses[k]:
            print("   ", row)
    s1 = {r[:3] for r in chain_misses["R1"]}
    s2 = {r[:3] for r in chain_misses["R2"]}
    print(f"\nR2 misses are a subset of R1's: {s2 <= s1}   only-R2: {sorted(s2 - s1)}")


if __name__ == "__main__":
    main()
