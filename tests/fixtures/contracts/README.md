# Contract fixture pack — draft, not the Milestone-0 deliverable

This is a small **starter** set (4 source items / 4 snapshots / 5 facts /
1 change set / 1 digest), built solo against the real
`docs/API_CONTRACT.md` shape so intelligence code has something
schema-valid to run against. It is **not** what `docs/TEAM_WORKFLOW.md`'s
"Day-one agreement" item 5 and `docs/ARCHITECTURE.md`'s Milestone 0
actually require:

> A committed, schema-validated fixture pack in `tests/fixtures/contracts/`:
> at least twenty `SourceItem` records with snapshots, two change sets,
> and two digests. It must include duplicate, changed-content,
> malformed-input, missing-evidence, and prompt-injection cases.

None of the edge cases (malformed input, missing evidence, a prompt-
injection string embedded in content) are represented here yet — that's
deliberately a joint exercise so ingestion, intelligence, and delivery
tests all validate against the same examples, per the Milestone-0
description. Replace these files wholesale when the team does that
session; don't just append to them solo.

## What this starter set demonstrates

- `a...0001` / `a...0002` — same real-world event (GPT-4o's context window
  increase) covered by two different publishers, sharing `event_id`
  (the "same story, different outlet" case).
- `a...0003` — the prior state (GPT-4o's original 128k window at launch),
  giving `change_sets.json` a real previous/current pair to cite.
- `a...0004` — a sparse item (Anthropic/Claude) whose snapshot explicitly
  states its context window isn't published yet — `extracted_facts.json`'s
  5th entry (`g...0005`) records that as a real, grounded
  `disclosure_status: "not_disclosed"` fact (ADR 0006), backing
  `digests.json`'s second claim ("has not disclosed its context window")
  with actual evidence rather than an inferred absence.
- `change_sets.json` — one `ChangeSet` containing one `Change`, both
  `previous` and `current` citing real snapshot ids and source URLs.
- `extracted_facts.json` — one `ExtractedFact` per (snapshot, field),
  all `extraction_method: "llm_structured_output"` with a model + prompt
  version recorded, per the contract's reproducibility requirement.
  4 `disclosure_status: "disclosed"` facts plus the 1 `"not_disclosed"`
  one described above (ADR 0006).
