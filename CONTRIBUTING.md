# Contributing

## Before taking a task

1. Create or select one clearly scoped issue with acceptance criteria.
2. Claim the issue by recording its active author, review steward, and expected files. Any teammate
   may author work in any module; the steward is responsible for design context and review, not
   exclusive implementation.
3. Confirm that no other active issue is modifying the same file set.
4. Check whether it alters a shared model, API, migration, or architecture decision.
5. Create a branch such as `feat/123-openai-rss`, `fix/142-dedup-timezone`, or `docs/18-source-policy`.

## Development flow

```bash
git pull --ff-only
make bootstrap
make check
```

Commit a coherent unit of work. Rebase or update from `main` before requesting final review, and resolve conflicts with the person who owns the affected module.

## Pull requests

- Keep a PR focused on one outcome; prefer fewer than about 400 changed lines excluding fixtures and generated lockfiles.
- Link the issue and describe behavior, risks, verification, data/schema changes, and rollback.
- Obtain at least one teammate review.
- When working outside your usual module, request review from that module's review steward. If the
  steward authored the change, another teammate provides the required non-author approval.
- Obtain a shared-contract owner review for changes under `shared`, migrations, or public API schemas.
- All required CI checks must pass before merge.
- Use squash merge so one PR produces one understandable commit on `main`.
- Delete the branch after merge.

## Definition of done

- Acceptance criteria are satisfied.
- Tests cover normal and failure paths.
- Formatting, linting, typing, tests, and security checks pass.
- Public behavior and architecture decisions are documented.
- Logs and errors are useful without exposing secrets or personal data.
- The change can be rolled back or disabled safely.
- A reviewer other than the author approves it.
