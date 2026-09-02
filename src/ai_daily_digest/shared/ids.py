"""ID generation. docs/API_CONTRACT.md requires opaque, canonical
lowercase hyphenated RFC 9562 UUID v7 identifiers on the wire, generated
by the application before persistence -- see
docs/adr/0007-uuid-v7-identifier-strategy.md.

This module is the ONLY place the third-party UUID dependency
(`uuid_utils`) may be imported. Every other module calls `new_id()`,
never `uuid_utils` directly (ADR 0007's Decision section)."""

from __future__ import annotations

import uuid

import uuid_utils.compat as _uuid_utils
from pydantic import UUID7


def new_id() -> uuid.UUID:
    """Generate a new resource identifier.

    Returns a standard-library `uuid.UUID`, not `str` -- `new_id()` is
    the stable public generator name (it does not encode the UUID
    version, so callers never depend on that); its Python-level
    representation is `uuid.UUID` end to end, matching `Uuid7Id` below,
    so no `str()` coercion is needed at any internal call site. The
    canonical lowercase hyphenated wire form comes for free from
    Pydantic's default `UUID` JSON serialization, or from `str(new_id())`
    directly.

    Generated through `uuid_utils.compat.uuid7()` -- Python 3.12 has no
    standard-library UUID v7 generator (added in Python 3.14; see ADR
    0007's Context section), so UUID v7 requires this vetted third-party
    dependency. `uuid_utils.compat.uuid7()` is verified (ADR 0007's
    Decision section) to return a genuine `uuid.UUID` instance -- not
    `uuid_utils`'s own Rust-backed type -- with `.version == 7` and
    correct RFC 9562 variant bits. No hand-written UUID v7 generation
    logic exists here or anywhere else in this codebase; this function's
    only job is calling into the one approved dependency."""
    return _uuid_utils.uuid7()


# The one central type every UUID v7 identifier field in shared/schemas.py
# uses for runtime validation at the model boundary -- a direct re-export
# of Pydantic's own built-in `UUID7` type
# (`Annotated[uuid.UUID, UuidVersion(7)]`), not a separately reconstructed
# validator (ADR 0007's Decision section: "do not substitute a different
# validation approach ... without amending this ADR first"). Defined
# here, next to the generator, so "what a UUID v7 identifier is" and "how
# one is generated" live in the same module. A field typed `Uuid7Id`
# holds a real `uuid.UUID` at the Python level once validated -- the same
# type `new_id()` returns, so there is one explicit representation end to
# end, never coercion at scattered call sites.
type Uuid7Id = UUID7
