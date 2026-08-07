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
pull request, which you can merge yourself — no approving review is required.

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
