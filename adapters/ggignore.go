// ggignore: the go-git side of the level-2 adapter. Reads one JSON request per line on stdin,
// writes one JSON answer per line on stdout. The tree is already on disk when this runs -- the
// Python adapter writes it, the same way it does for every other subject in this bench, so the
// filesystem layout is not something Go gets to reinterpret.
//
// Build it (needs the network once, for the module):
//
//	mkdir -p /tmp/ggb && cp ggignore.go /tmp/ggb/main.go && cd /tmp/ggb
//	go mod init ggignore && go get github.com/go-git/go-git/v5@v5.19.2
//	go build -o ggignore .
//
// Then point the adapter at it with --helper /tmp/ggb/ggignore (or $GO_GIT_HELPER); if neither is
// given it looks for a file called `ggignore` next to the adapter.
//
// Two entry points, because a library is not automatically one subject:
//
//	"patterns": gitignore.ReadPatterns(fs, nil) + gitignore.NewMatcher(ps).Match(path, isDir).
//	            This is the layer go-git promises: walk the tree, collect every rule file,
//	            answer for any path. It is what a caller holds directly.
//	"status":   Worktree.Status(), which is what Gitea, ArgoCD and Flux reach through. go-git
//	            calls ReadPatterns itself there (worktree_status.go), so it should agree -- and
//	            "should" is why it is runnable. Files only: Status reports files, so a directory
//	            query is declined rather than guessed.
//
// The version go-git reports for itself, and the count of global/system patterns visible from
// here, are printed to stderr at startup. Not taken on faith: a subject's defaults answer a
// different question than the one being asked, and the only way to know is to print them.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"

	"github.com/go-git/go-billy/v5/osfs"
	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing/format/gitignore"
	"github.com/go-git/go-git/v5/storage/filesystem"
	"github.com/go-git/go-git/v5/plumbing/cache"
)

type request struct {
	ID      interface{} `json:"id"`
	Root    string      `json:"root"`
	Queries []string    `json:"queries"`
	// Declined by the Python side (a query that could not be materialised on disk). Passed
	// through as null rather than answered, so a filesystem limitation never looks like a verdict.
	Declined []string `json:"declined"`
}

type response struct {
	ID      interface{} `json:"id"`
	Ignored []*bool     `json:"ignored"`
	Error   string      `json:"error,omitempty"`
}

func boolp(b bool) *bool { return &b }

// splitPath turns "a/b/c" into the []string go-git wants. A trailing slash means "directory" in
// the corpus and in `git check-ignore`; it is not part of the name.
func splitPath(q string) ([]string, bool) {
	isDir := strings.HasSuffix(q, "/")
	trimmed := strings.Trim(q, "/")
	if trimmed == "" {
		return nil, isDir
	}
	return strings.Split(trimmed, "/"), isDir
}

func answerPatterns(root string, queries []string, declined map[string]bool) ([]*bool, error) {
	fs := osfs.New(root)
	ps, err := gitignore.ReadPatterns(fs, nil)
	if err != nil {
		return nil, err
	}
	m := gitignore.NewMatcher(ps)
	out := make([]*bool, 0, len(queries))
	for _, q := range queries {
		if declined[q] {
			out = append(out, nil)
			continue
		}
		parts, isDir := splitPath(q)
		out = append(out, boolp(m.Match(parts, isDir)))
	}
	return out, nil
}

// answerStatus asks the porcelain: a path is "ignored" if go-git's own Status does not report it
// as untracked. The tree carries no commits and nothing is staged, so every file that is not
// ignored must show up as untracked -- which is exactly the property being leaned on.
func answerStatus(root string, queries []string, declined map[string]bool) ([]*bool, error) {
	// Open first, init only if that fails: `ErrRepositoryAlreadyExists` moved between v5 and v6,
	// and this file has to compile unchanged against both or the two columns stop comparing the
	// same harness.
	repo, err := git.PlainOpen(root)
	if err != nil {
		repo, err = git.PlainInit(root, false)
		if err != nil {
			return nil, err
		}
	}
	wt, err := repo.Worktree()
	if err != nil {
		return nil, err
	}
	st, err := wt.Status()
	if err != nil {
		return nil, err
	}
	untracked := map[string]bool{}
	for path, fst := range st {
		if fst.Worktree == git.Untracked {
			untracked[path] = true
		}
	}
	out := make([]*bool, 0, len(queries))
	for _, q := range queries {
		if declined[q] || strings.HasSuffix(q, "/") {
			// Status reports files. A directory has no row of its own, and inferring one from
			// its children would be my rule, not go-git's.
			out = append(out, nil)
			continue
		}
		out = append(out, boolp(!untracked[strings.Trim(q, "/")]))
	}
	return out, nil
}

// report prints what this binary actually is, and what it can see from here, before answering
// anything. A subject measured with its defaults unstated is a subject measured twice.
func report(entry string) {
	version := "unknown"
	if bi, ok := debug.ReadBuildInfo(); ok {
		for _, dep := range bi.Deps {
			if strings.HasPrefix(dep.Path, "github.com/go-git/go-git/") {
				version = dep.Path + " " + dep.Version
			}
		}
	}
	fmt.Fprintf(os.Stderr, "ggignore: %s, entry=%s\n", version, entry)

	home, _ := os.UserHomeDir()
	globals, _ := gitignore.LoadGlobalPatterns(osfs.New(home))
	systems, _ := gitignore.LoadSystemPatterns(osfs.New("/"))
	fmt.Fprintf(os.Stderr, "ggignore: global patterns visible from %s: %d, system: %d "+
		"(ReadPatterns does not load either; printed as a control)\n", home, len(globals), len(systems))

	// Touch the storer package so a build that drops it fails loudly rather than silently
	// changing what `status` means.
	_ = filesystem.NewStorage
	_ = cache.NewObjectLRUDefault
}

func main() {
	entry := "patterns"
	for i, a := range os.Args[1:] {
		if a == "--entry" && i+2 <= len(os.Args[1:]) {
			entry = os.Args[i+2]
		} else if strings.HasPrefix(a, "--entry=") {
			entry = strings.SplitN(a, "=", 2)[1]
		}
	}
	if entry != "patterns" && entry != "status" {
		fmt.Fprintf(os.Stderr, "--entry must be 'patterns' or 'status', got %q\n", entry)
		os.Exit(2)
	}
	report(entry)

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
			fmt.Fprintf(os.Stderr, "bad request: %v\n", err)
			os.Exit(2)
		}
		declined := map[string]bool{}
		for _, d := range req.Declined {
			declined[d] = true
		}
		root, err := filepath.Abs(req.Root)
		resp := response{ID: req.ID}
		if err == nil {
			var ans []*bool
			if entry == "patterns" {
				ans, err = answerPatterns(root, req.Queries, declined)
			} else {
				ans, err = answerStatus(root, req.Queries, declined)
			}
			resp.Ignored = ans
		}
		if err != nil {
			resp.Error = err.Error()
		}
		enc, _ := json.Marshal(resp)
		out.Write(enc)
		out.WriteByte('\n')
		out.Flush()
	}
}
