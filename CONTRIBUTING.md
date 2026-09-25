# Contributing

## Branches

Every branch, including your own working branches, follows:

```
user/<github-username>/<short-feature-or-design-slug>
```

Example: `user/adityamunot/headroom-rl-mvp`. Lowercase, hyphen-separated slug — describe the feature or design, not a ticket number alone.

## Commits

Commit in logical, reviewable units — not one commit bundling an entire feature or session's work. Split by concern: scaffolding/config, library code, tests, examples, docs are typically separate commits even when they land in the same PR. Each commit should stand on its own for review, with a message that explains why, not just what changed line by line.

## Provider parsers

See [examples/providers/README.md](examples/providers/README.md).
