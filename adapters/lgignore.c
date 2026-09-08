/* lgignore -- ask libgit2 the same question `git check-ignore` asks git.
 *
 *   usage:  lgignore <workdir>      repo-relative paths on stdin, one per line
 *   output: "1" ignored, "0" not ignored, "E <msg>" error -- one line per query
 *
 * One process per tree, like every other adapter here. Two settings on the subject's side,
 * neither of them shading the answer: both put libgit2 and git into the *same* question.
 *
 *   1. The system / global / XDG config search paths are emptied. Otherwise libgit2 would read
 *      this machine's `core.excludesFile` and git -- invoked with `--no-index` by the oracle --
 *      would not. It is the equivalent of ripgrep's `--no-ignore-global`, and PROTOCOL.md puts
 *      machine state out of scope explicitly.
 *   2. The repository is initialised by libgit2 itself rather than by the `git` binary, so the
 *      subject sees exactly the tree the bench wrote and nothing a template dropped in.
 *
 * Build it against whichever libgit2 you mean to measure -- the version it prints on stderr at
 * startup is the one being scored, and the point of measuring is not to assume it:
 *
 *   cmake <libgit2-src> -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
 *         -DBUILD_TESTS=OFF -DBUILD_CLI=OFF -DBUILD_EXAMPLES=OFF \
 *         -DUSE_HTTPS=OFF -DUSE_SSH=OFF -DUSE_HTTP_PARSER=builtin \
 *         -DUSE_BUNDLED_ZLIB=ON -DREGEX_BACKEND=builtin
 *   make -j4
 *   cc -O2 -o lgignore lgignore.c -I<libgit2-src>/include -I<build>/include \
 *      <build>/libgit2.a -lpthread -lz
 *
 * Then point `libgit2_adapter.py` at it with `--bin ./lgignore` or `$LIBGIT2_ORACLE`.
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <git2.h>

int main(int argc, char **argv)
{
	git_repository *repo = NULL;
	char line[8192];
	int err;

	if (argc < 2) {
		fprintf(stderr, "usage: lgignore <workdir>\n");
		return 2;
	}

	git_libgit2_init();

	git_libgit2_opts(GIT_OPT_SET_SEARCH_PATH, GIT_CONFIG_LEVEL_SYSTEM, "");
	git_libgit2_opts(GIT_OPT_SET_SEARCH_PATH, GIT_CONFIG_LEVEL_GLOBAL, "");
	git_libgit2_opts(GIT_OPT_SET_SEARCH_PATH, GIT_CONFIG_LEVEL_XDG, "");
	git_libgit2_opts(GIT_OPT_SET_SEARCH_PATH, GIT_CONFIG_LEVEL_PROGRAMDATA, "");

	if ((err = git_repository_init(&repo, argv[1], 0)) < 0) {
		const git_error *e = git_error_last();
		fprintf(stderr, "init: %s\n", e ? e->message : "?");
		return 3;
	}

	/* the subject's version, on stderr: printed, not assumed */
	{
		int major, minor, rev;
		git_libgit2_version(&major, &minor, &rev);
		fprintf(stderr, "libgit2 %d.%d.%d\n", major, minor, rev);
	}

	while (fgets(line, sizeof(line), stdin)) {
		size_t n = strlen(line);
		int ignored = -1;

		while (n && (line[n - 1] == '\n' || line[n - 1] == '\r'))
			line[--n] = '\0';
		if (!n) {
			printf("E empty\n");
			fflush(stdout);
			continue;
		}

		if (git_ignore_path_is_ignored(&ignored, repo, line) < 0) {
			const git_error *e = git_error_last();
			printf("E %s\n", e && e->message ? e->message : "?");
		} else {
			printf("%d\n", ignored ? 1 : 0);
		}
		fflush(stdout);
	}

	git_repository_free(repo);
	git_libgit2_shutdown();
	return 0;
}
