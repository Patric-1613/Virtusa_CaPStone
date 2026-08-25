# Claude Code Prompt — CODEOWNERS and Git Collaboration Setup

Copy everything below this line into Claude Code while its terminal is open at the repository root.

---

We need to review and implement the local GitHub ownership configuration for the three-person AI
Daily Digest project.

## Working mode

Use two phases:

1. **Review phase:** inspect the repository, report concerns, and show the exact file-level plan.
   Do not edit anything during this phase.
2. **Implementation phase:** wait for my explicit approval of the plan. After approval, make the
   agreed local file changes and validate them.

Do not stage, commit, push, create a GitHub repository, add a remote, invite collaborators, change
GitHub settings, or open a pull request unless I separately and explicitly authorize those external
Git actions. The local repository currently may have no commits and no configured remote; verify
instead of assuming.

## Required repository reading

Before proposing changes, read completely:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `CONTRIBUTING.md`
- `docs/ARCHITECTURE.md`
- `docs/API_CONTRACT.md`
- `docs/ENGINEERING_STANDARDS.md`
- `docs/TEAM_WORKFLOW.md`
- `docs/adr/README.md`
- `.github/pull_request_template.md`
- `.github/workflows/ci.yml`
- `.gitignore`

Also inspect:

- `git status --short`
- `git branch --show-current`
- `git remote -v`
- the current `src/ai_daily_digest/` directory structure
- whether `.github/CODEOWNERS` already exists

Preserve all unrelated and uncommitted work.

## Confirmed team mapping

| Role | GitHub account | Primary responsibility |
|---|---|---|
| Person A | `@Patric-1613` | Ingestion: sources, collectors, normalization, snapshots, provenance, deduplication |
| Person B | `@SujinJK` | Intelligence: fact extraction, historical comparison, LangGraph workflows, digest generation, evaluation |
| Person C | `@chamath-wijayasundara` | Delivery: API, frontend-facing services, subscriptions, email, chat, deployment adapters |

Shared contracts, API schemas, database migrations, architecture decisions, engineering policies,
and ownership configuration are shared responsibilities.

The accounts must have repository write access before GitHub can treat them as valid code owners.
Do not claim that access has been verified unless repository access can actually be inspected.

## Desired collaboration behavior

The ownership configuration should:

- automatically route pull-request review requests to people familiar with the changed area;
- communicate primary responsibility without preventing teammates from contributing elsewhere;
- ensure every change receives at least one human teammate review;
- avoid requiring all three people to approve every ordinary module change;
- avoid blocking the team whenever one person is unavailable;
- give shared contracts and architectural changes broader visibility;
- protect the ownership configuration itself from silent changes;
- remain simple enough for three interns to understand and maintain.

Initially, recommend branch protection requiring one approval from someone other than the author.
Treat “Require review from Code Owners” as an optional later setting after the team tests the review
workflow. Explain that `CODEOWNERS` routes review requests even when code-owner approval is not a
mandatory merge gate.

## Ownership design to evaluate

Do not accept this design blindly. Review GitHub CODEOWNERS precedence and multiple-owner behavior,
then recommend the smallest correct mapping.

Preferred starting approach:

- Person A is the primary owner for `src/ai_daily_digest/ingestion/`, `sources.yaml`, and Part A
  source research.
- Person B is the primary owner for `src/ai_daily_digest/intelligence/`.
- Person C is the primary owner for `src/ai_daily_digest/delivery/`.
- All three own `src/ai_daily_digest/shared/`, `docs/API_CONTRACT.md`, architecture/ADR files,
  database migrations when added, CI/quality policy, and `.github/CODEOWNERS`.
- Each module should have at least one practical peer-review route when its primary owner authors
  the pull request. Decide whether this belongs in CODEOWNERS as a backup owner or is better
  enforced by the general one-approval branch rule.
- Avoid a global `*` rule that requests all three people for every low-risk file unless you can
  justify the notification noise.

Important GitHub behavior to account for:

- Later matching CODEOWNERS patterns take precedence over earlier matching patterns.
- Listing multiple owners normally means any one owner can satisfy a required code-owner approval;
  it does not require every listed owner to approve.
- CODEOWNERS does not itself prevent edits or grant repository access.
- Required approvals and status checks are enforced by GitHub branch protection or rulesets, not by
  the file alone.
- Review requests use the CODEOWNERS file from the pull request's base branch. Therefore, the first
  pull request that introduces CODEOWNERS may need reviewers requested manually.

## Files that may be changed after approval

Limit implementation to the smallest justified set:

1. `.github/CODEOWNERS` — create the reviewed ownership patterns.
2. `docs/TEAM_WORKFLOW.md` — replace generic role-only ownership references with the confirmed
   GitHub mapping where that improves clarity, and document the lightweight review policy.

Do not update `AGENTS.md`, `CLAUDE.md`, architecture, API contracts, ADR statuses, source code,
dependencies, or CI unless the review finds a concrete contradiction that blocks a correct
CODEOWNERS setup. If such a blocker exists, report it and wait instead of expanding scope.

## Required review-phase output

Before editing, provide:

1. Verdict: `ACCEPT`, `ACCEPT WITH CHANGES`, or `REJECT` for the preferred ownership design.
2. A short explanation of what CODEOWNERS will and will not control.
3. The exact proposed `.github/CODEOWNERS` content.
4. A table with each pattern, owner(s), reason, and likely review behavior.
5. Any risk of self-review deadlock, excessive notifications, uncovered files, or misleading
   ownership.
6. Exact changes proposed for `docs/TEAM_WORKFLOW.md`.
7. GitHub settings that must be completed manually after pushing.
8. Any missing information, especially the repository URL, collaborator access, default branch,
   or repository plan limitations.
9. The validation commands you will run after implementation.
10. A clear pause asking me to approve or amend the plan.

## Implementation rules after approval

After I explicitly approve the plan:

1. Use the repository's normal editing process and make only the agreed file changes.
2. Keep CODEOWNERS patterns rooted and narrowly scoped where practical.
3. Add comments explaining the three module boundaries and shared areas.
4. Do not claim GitHub accepted the ownership entries until the file is on GitHub and collaborator
   permissions are verified.
5. Inspect the final diff and confirm no unrelated files changed.
6. Run appropriate documentation/configuration validation. Run `make check` if required by the
   repository rules; run `make ci` before any later pull request is opened.
7. Report exactly what changed, what passed, and what still requires manual GitHub configuration.
8. Stop without staging, committing, pushing, inviting users, changing branch protection, or
   opening a pull request.

## Expected manual GitHub work after local implementation

Report these as later actions, not as completed work:

1. Confirm the repository URL and configure `origin` if it is absent.
2. Ensure `@SujinJK` and `@chamath-wijayasundara` have accepted collaborator invitations and have
   write access.
3. Push the engineering-foundation review branch only after an explicit authorization.
4. Manually request both teammates on the first pull request if CODEOWNERS is not yet on its base
   branch.
5. After merging the foundation, protect the default branch with:
   - pull requests required;
   - at least one approval from someone other than the author;
   - stale approvals dismissed after relevant new commits;
   - conversation resolution required;
   - `quality`, `tests`, and `security` status checks required;
   - force pushes and branch deletion blocked.
6. Consider enabling required code-owner approval only after the team confirms it does not create
   avoidable blocking.

The goal is clear review routing and shared responsibility, not ownership bureaucracy.
