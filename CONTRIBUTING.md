# Contributing

## Setup

```bash
pip install -e ".[dev]"
git config core.hooksPath hooks     # see "Git hooks" below
pytest
```

The suite monkeypatches the RAW decode, so it needs no sample files and runs in
about a second.

## Git hooks

The repo keeps its hooks in the tracked `hooks/` directory rather than
`.git/hooks/`, so the scripts are versioned along with the code. Git does not
look there on its own — point it at the directory once per clone:

```bash
git config core.hooksPath hooks
```

That setting is local to the clone; it cannot be committed, so every fresh clone
needs the command again. Nothing breaks without it, you just lose the local
check described next.

`hooks/pre-push` refuses to push to `main`. It matches on the *remote* ref, so
`git push origin somebranch:main` is caught too, not only a push made while on
`main`. `git push --no-verify` bypasses it, as with any hook.

## Branches and pull requests

`main` is protected on GitHub: it takes no direct pushes, no force pushes and no
deletions, and the rule is enforced for admins too. Every change lands through a
pull request, which you can merge yourself — no approving review is required,
but CI must be green (see below).

```bash
git switch -c my-change
# ... work, then ...
pytest
git push -u origin my-change
gh pr create
```

The pre-push hook is only a local convenience that fails fast; GitHub's branch
protection is the actual guarantee, and it holds whether or not the hook is
installed.

## CI

`.github/workflows/ci.yml` runs `pytest` on every pull request and on pushes to
`main`, against Python 3.9, 3.11 and 3.13 — the ends of the range declared in
`pyproject.toml`, plus one in the middle. A run takes well under a minute.

A second job, `droplet`, builds `contrib/macos` on a macOS runner, which is how
`build-app.sh`'s self-tests get run. `pytest` cannot reach any of that: the
droplet is AppleScript. The job installs the package first, so the build takes
its document extensions from `trichrome.RAW_EXTENSIONS` rather than the
hardcoded fallback — the two drifting apart is the failure worth catching.

Merging into `main` requires exactly one status check, `ci-ok`. That job does no
testing of its own: it depends on the whole matrix and on `droplet`, and passes
only when all of them passed. The indirection is deliberate — branch protection
matches checks by *name*, so requiring `test (3.9)` and friends directly would
mean that editing the matrix silently wedges every pull request, waiting forever
on a check that no longer reports under that name. Requiring `ci-ok` instead
leaves the matrix free to change.

For the same reason `ci-ok` is marked `if: always()`. A skipped check never
reports, so without it a failing matrix would hang pull requests rather than
turning them red.

If you do change the matrix, `ci-ok` needs no update — but a wholly new job
does need adding to its `needs`, or it will not gate anything. If you rename the
`ci-ok` job itself, update the required check on `main` in the same breath or
nothing will be mergeable.
