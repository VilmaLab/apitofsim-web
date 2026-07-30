# AGENTS.md

## Style — DRY and reuse first

Follow the patterns already in the codebase rather than inventing new ones.
Be succinct!

## Workflow

Work in a git worktree per task, never directly on `main`:

```sh
git worktree add ../apitofsim-web-<task> -b <task>
```

Commit there, push with `git push -u origin <task>`, and open a pull request
with `gh pr create`. Clean up with `git worktree remove` after merge.
