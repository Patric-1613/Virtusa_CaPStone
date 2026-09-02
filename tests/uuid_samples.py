"""Shared, frozen UUID v7 test values (ADR 0007), for descriptive
identifiers reused across more than one test file -- e.g. the generic
"a source item exists" filler every `_snapshot()` test helper needs for
`source_item_id`, or "a syntactically valid but never-registered
snapshot id" used by several citation-validation test suites.

A value needed by only one test file does not belong here -- define it
as a local module-level constant in that file instead (see e.g.
tests/unit/test_daily_run.py's own TDR_*-prefixed constants).

Every value below was generated once, offline, via the actual approved
generator (`uuid_utils.compat.uuid7(timestamp=<int epoch seconds>)` --
the same function `shared/ids.py::new_id()` calls in production) and
self-validated (parsed, `.version == 7`, correct RFC 9562 variant bits)
before being frozen here as a literal. Never regenerated at test-run
time -- these are plain constants, not a runtime generator call.
"""

from __future__ import annotations

import uuid

# A generic, reusable "some source item exists" filler -- used as
# source_item_id by test helpers that build a DocumentSnapshot but don't
# care which SourceItem it belongs to.
ITEM_1 = uuid.UUID("01a01e2f-2000-71d0-b48b-746047c81212")

# Generic, reusable snapshot ids for tests where the specific value
# doesn't matter, only that citations/known-id sets agree on it.
SNAPSHOT_1 = uuid.UUID("01a01e33-b3e0-7623-9809-2ef2dc7e6ede")
SNAPSHOT_2 = uuid.UUID("01a01e38-47c0-7b40-b5ea-4c388a5be9b3")
SNAPSHOT_3 = uuid.UUID("01a01e3c-dba0-7092-b0e6-16415b435cff")

# A syntactically valid UUID v7 that is deliberately never registered in
# any resolver/known-id set in a given test -- stands in for the old
# "snap_missing"/"snap_does_not_exist" placeholder pattern.
SNAPSHOT_MISSING = uuid.UUID("01a01e41-6f80-70f3-8826-538744bd94f3")

# Generic DigestClaim ids for tests where the value itself is never
# asserted on, only used to distinguish claims from one another.
CLAIM_1 = uuid.UUID("01a01e66-0e80-7ca1-aad1-eca090cd0970")
CLAIM_2 = uuid.UUID("01a01e66-f8e0-7d61-8bc7-ecb00fc85853")
CLAIM_3 = uuid.UUID("01a01e67-e340-7273-a5b6-eb7065225f8f")

# A generic Digest id -- never asserted on directly in the test files
# that use it, only structurally required by the model.
DIGEST_1 = uuid.UUID("01a02054-7100-75c2-bd92-d77265e492b3")

# A generic ChangeSet id -- same reasoning as DIGEST_1.
CHANGE_SET_1 = uuid.UUID("01a01e6f-3640-7690-ba8d-2a6f322393cb")

# A generic Change id -- same reasoning as DIGEST_1.
CHANGE_1 = uuid.UUID("01a01e70-20a0-7fb1-851c-733ecea935db")

# A generic ExtractedFact id -- same reasoning as DIGEST_1.
FACT_1 = uuid.UUID("01a01e34-9e40-7c83-bc72-e83602911e38")
