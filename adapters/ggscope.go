// ggscope: go-git's answer to the `between` question -- "does a walk descend into this
// directory?" -- rather than "is this path ignored?". Same wire protocol as `ggignore.go` (one
// JSON request per line on stdin, one answer per line on stdout), so the Python adapter drives it
// unchanged: point it here with --helper and --entry scope.
//
// Why a separate binary instead of a third `--entry` in ggignore.go: the API this asks about does
// not exist in the released go-git. `gitignore.Scope` landed on `main` (see scope.go, "Reference
// git tracks the same state in its walk rather than recomputing it per query"), and v5.19.2 has no
// such type, so a single file could not compile against both trees. Keeping the two binaries apart
// says that out loud instead of hiding it behind a build tag: measuring the prune door on go-git
// means measuring `main`, and the release simply does not offer the door.
//
// The door, and why it is the one the corpus asks for. `Matcher.Match(dir, true)` answers "is this
// directory ignored", which is a different question -- git never descends into an excluded
// directory, so what the walk needs is the state, not the per-query verdict. `Scope` carries that
// state: Descend derives the child scope and refuses to read ignore files below an excluded one,
// and Excluded() reports it. That is the same shape as dulwich's `may_prune_directory`, and it is
// what `worktree_status.go` itself uses to avoid walking into node_modules.
//
// So: for `a/b/`, start from NewScope(RootPatterns(fs)) and Descend once per component, feeding
// each level's own rules through DirPatterns. The verdict is the final scope's Excluded().
// Directory queries only; a file query is declined (null), because a file is not descended into
// and inventing a row for it would be my rule, not go-git's.
//
// Build it against a go-git tree that has Scope (module path v6):
//
//	mkdir -p /tmp/ggs && cp ggscope.go /tmp/ggs/main.go && cd /tmp/ggs
//	go mod init ggscope && go mod edit -replace github.com/go-git/go-git/v6=<tree> && go mod tidy
//	go build -o ggscope .
//
// The go-git version it was built from goes to stderr at startup. Not taken on faith from a go.mod
// I did not build: a subject measured with its provenance unstated is a subject measured twice.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"

	"github.com/go-git/go-billy/v6/osfs"
	"github.com/go-git/go-git/v6/plumbing/format/gitignore"
)

type request struct {
	ID      interface{} `json:"id"`
	Root    string      `json:"root"`
	Queries []string    `json:"queries"`
	// Declined by the Python side (a query it could not materialise on disk). Passed through as
	// null rather than answered, so a filesystem limitation never looks like a verdict.
	Declined []string `json:"declined"`
}

type response struct {
	ID      interface{} `json:"id"`
	Ignored []*bool     `json:"ignored"`
	Error   string      `json:"error,omitempty"`
}

func boolp(b bool) *bool { return &b }

// answerScope derives one Scope per directory, memoised, the way a walk would: each directory's
// scope comes from its parent's, and the rules of a directory below an excluded one are never
// read, because Descend does not call readOwn there.
func answerScope(root string, queries []string, declined map[string]bool) ([]*bool, error) {
	fs := osfs.New(root)
	base, err := gitignore.RootPatterns(fs)
	if err != nil {
		// A root that cannot be read yields no patterns, which is what go-git's own
		// `ignoreScope()` does. Erroring here would report my filesystem as a divergence.
		base = nil
	}
	cache := map[string]*gitignore.Scope{"": gitignore.NewScope(base)}

	var scopeFor func(parts []string) (*gitignore.Scope, error)
	scopeFor = func(parts []string) (*gitignore.Scope, error) {
		key := strings.Join(parts, "/")
		if s, ok := cache[key]; ok {
			return s, nil
		}
		parent, err := scopeFor(parts[:len(parts)-1])
		if err != nil {
			return nil, err
		}
		dir := append([]string(nil), parts...)
		child, err := parent.Descend(dir, func() ([]gitignore.Pattern, error) {
			return gitignore.DirPatterns(fs, dir)
		})
		if err != nil {
			return nil, err
		}
		cache[key] = child
		return child, nil
	}

	out := make([]*bool, 0, len(queries))
	for _, q := range queries {
		if declined[q] || !strings.HasSuffix(q, "/") {
			out = append(out, nil)
			continue
		}
		trimmed := strings.Trim(q, "/")
		if trimmed == "" {
			out = append(out, nil)
			continue
		}
		// A directory that is not on disk was never a candidate for the walk to prune. Same
		// rule as the libgit2 walk adapter: no evidence is null, never a guess.
		if st, err := os.Stat(filepath.Join(root, trimmed)); err != nil || !st.IsDir() {
			out = append(out, nil)
			continue
		}
		scope, err := scopeFor(strings.Split(trimmed, "/"))
		if err != nil {
			return nil, err
		}
		out = append(out, boolp(scope.Excluded()))
	}
	return out, nil
}

func report() {
	version := "unknown"
	if bi, ok := debug.ReadBuildInfo(); ok {
		for _, dep := range bi.Deps {
			if strings.HasPrefix(dep.Path, "github.com/go-git/go-git/") {
				version = dep.Path + " " + dep.Version
			}
		}
	}
	fmt.Fprintf(os.Stderr, "ggscope: %s, entry=scope (Scope.Descend + Excluded)\n", version)
}

func main() {
	entry := "scope"
	for i, a := range os.Args[1:] {
		if a == "--entry" && i+2 <= len(os.Args[1:]) {
			entry = os.Args[i+2]
		} else if strings.HasPrefix(a, "--entry=") {
			entry = strings.SplitN(a, "=", 2)[1]
		}
	}
	if entry != "scope" {
		fmt.Fprintf(os.Stderr, "ggscope only answers --entry scope, got %q\n", entry)
		os.Exit(2)
	}
	report()

	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1024*1024), 64*1024*1024)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()

	for in.Scan() {
		line := strings.TrimSpace(in.Text())
		if line == "" {
			continue
		}
		var req request
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			fmt.Fprintf(os.Stderr, "ggscope: bad request: %v\n", err)
			os.Exit(2)
		}
		declined := map[string]bool{}
		for _, d := range req.Declined {
			declined[d] = true
		}
		resp := response{ID: req.ID}
		ignored, err := answerScope(req.Root, req.Queries, declined)
		if err != nil {
			resp.Error = err.Error()
		} else {
			resp.Ignored = ignored
		}
		enc, _ := json.Marshal(resp)
		out.WriteString(string(enc) + "\n")
		out.Flush()
	}
}
