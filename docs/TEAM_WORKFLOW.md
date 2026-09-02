# Three-Person Team Workflow

## Ownership without silos

| Area | Review steward | Required reviewer |
|---|---|---|
| Ingestion | Person A (`@Patric-1613`) | B or C |
| Intelligence and digest | Person B (`@SujinJK`) | A or C |
| Delivery, frontend and deployment | Person C (`@chamath-wijayasundara`) | A or B |
| Shared contracts, API schema, database migrations | Change author | At least one affected module owner |

The review steward coordinates design and keeps the module healthy. The role does not reserve that
module's implementation work: any teammate may author a focused change in any area through a
reviewed pull request.

## Parallel authorship policy

- Any teammate may implement work in ingestion, intelligence, delivery, or shared code.
- Before starting, claim a focused issue and record the active author, review steward, acceptance
  criteria, and expected files.
- Keep one active author per issue or overlapping file set. Split parallel work by concern and file
  boundary rather than letting two branches independently rewrite the same implementation.
- When the author is not the area's review steward, request that steward's review. When the steward
  is the author, another teammate supplies the required non-author review.
- Shared models, public API contracts, database migrations, and architecture decisions still follow
  the ADR and cross-module review process. Flexible authorship does not bypass contract review.
- CI, branch protection, required checks, and the non-author approval rule remain unchanged.

## How this maps to GitHub

`.github/CODEOWNERS` encodes the review-steward column above and automatically
requests that person as a reviewer when a pull request touches their module. It does not restrict
which collaborator may create a branch, edit those files, or open the pull request.
Shared files (contracts, architecture, ADRs, engineering policy, CI config)
list all three owners; any one of the three can satisfy that review.

CODEOWNERS only routes review requests -- it does not by itself require
code-owner approval to merge. The actual merge gate is branch protection:
one approval from someone other than the author, on every pull request.
This means the "Required reviewer" column above is enforced by the general
approval rule, not by a hard-coded backup name in CODEOWNERS: if the review
steward is the PR author, any other teammate's approval satisfies the rule.

We are deliberately not enabling "Require review from Code Owners" yet.
Revisit that setting only after the team has used the lightweight routing
for a while, and only if each module pattern also gets a backup owner first
-- otherwise a solo-authored module PR could have no eligible code-owner
approver and become unmergeable.

## Day-one agreement

Before splitting work, approve:

1. ADR 0001: modular monolith.
2. ADR 0002: PostgreSQL plus pgvector.
3. ADR 0003: quality gates.
4. `docs/API_CONTRACT.md` v0.1.
5. A committed, schema-validated fixture pack in `tests/fixtures/contracts/`: at least twenty
   `SourceItem` records with snapshots, two change sets, and two digests. It must include duplicate,
   changed-content, malformed-input, missing-evidence, and prompt-injection cases.
6. GitHub branch protection and required checks.
7. Confirm that all three CODEOWNERS accounts have accepted repository access and have write
   permission.

`.github/CODEOWNERS` lists the primary module owners and lists all three teammates for shared
contracts, architecture, ADRs and engineering policy. Listing all three gives shared changes
broader visibility but does not require unanimous approval. Branch protection initially requires
one approval from someone other than the author. Verify ownership routing and account resolution
on a test pull request after the file reaches the default branch.

## Avoiding overwritten work

- One issue has one active author.
- Announce a shared-file change before editing it.
- Pull/rebase from `main` at least daily and before final review.
- Keep branches under two working days when possible.
- Do not mix formatting of unrelated files into a functional PR.
- If two tasks need the same shared contract, agree and merge that small contract PR first; both feature branches then build on it.
- Resolve semantic conflicts with the teammate who wrote the affected behavior, not by choosing whichever version makes Git quiet.

## Suggested issue shape

```text
Outcome: OpenAI RSS entries become normalized source items.
Active author: Person A
Review steward: Person A (another teammate approves because the steward is the author)
Expected files: sources.yaml and src/ai_daily_digest/ingestion/openai_feed.py
Inputs: saved RSS fixture and source registry entry
Outputs: SourceItem records and CollectionRun summary
Acceptance:
- duplicate rerun creates no duplicate item
- changed content creates a new snapshot
- malformed entry records an item error and continues
- unit tests use no live network
Out of scope: summarization and ranking
```

## Daily rhythm

- Ten-minute stand-up: finished, next outcome, blockers, and shared files/contracts likely to change.
- Work in small edits with the inner loop.
- Open a draft PR early enough for teammates to see the direction.
- Merge reviewed, green PRs continuously rather than saving integration for the end of the week.

## Agent workflow in VS Code

1. Give Claude Code one issue and acceptance criteria, not the whole product.
2. Ask it to inspect `AGENTS.md`, relevant ADRs, and the owning module before proposing a plan.
3. Approve the file-level plan before it edits shared areas.
4. Claude runs the narrow test and `make check`.
5. Review the diff yourself; do not accept an agent's statement that tests passed without terminal evidence.
6. Ask Codex to review the actual diff for correctness, architecture, security, provenance, and missing tests.
7. Correct findings, run `make ci`, then request human teammate review.

Claude Code can be the implementer and Codex can be a second reviewer, but neither replaces the teammate approval. Using two models can reduce correlated mistakes; it does not prove correctness.
