"""Initial persistence foundation migration — ADR 0002 & ADR 0011.

Revision ID: 0001
Revises:
Create Date: 2026-09-04 20:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

# Ensure all models are registered against Base.metadata
import ai_daily_digest.ingestion.db.models  # pylint: disable=unused-import
import ai_daily_digest.intelligence.db.models  # noqa: F401  # pylint: disable=unused-import
from ai_daily_digest.shared.db.metadata import Base
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERS_SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_daily_digest"
    / "intelligence"
    / "db"
    / "triggers.sql"
)


def upgrade() -> None:
    """Create all ingestion and intelligence tables, then apply immutability triggers."""
    Base.metadata.create_all(bind=op.get_bind())

    triggers_sql = TRIGGERS_SQL_PATH.read_text(encoding="utf-8")
    op.execute(triggers_sql)


def downgrade() -> None:
    """Drop immutability triggers and functions, then drop all tables."""
    op.execute("""
        DROP TRIGGER IF EXISTS trg_protect_source_items_immutability ON source_items;
        DROP TRIGGER IF EXISTS trg_protect_document_snapshots_immutable_update ON document_snapshots;
        DROP TRIGGER IF EXISTS trg_protect_document_snapshots_immutable_delete ON document_snapshots;
        DROP TRIGGER IF EXISTS trg_protect_document_snapshots_immutable_truncate ON document_snapshots;
        DROP TRIGGER IF EXISTS trg_validate_extracted_fact_observed_at ON extracted_facts;
        DROP TRIGGER IF EXISTS trg_protect_extracted_facts_immutable_update ON extracted_facts;
        DROP TRIGGER IF EXISTS trg_protect_extracted_facts_immutable_delete ON extracted_facts;
        DROP TRIGGER IF EXISTS trg_protect_extracted_facts_immutable_truncate ON extracted_facts;
        DROP TRIGGER IF EXISTS trg_validate_change_provenance ON changes;
        DROP TRIGGER IF EXISTS trg_protect_changes_immutability ON changes;
        DROP TRIGGER IF EXISTS trg_protect_changes_delete ON changes;
        DROP TRIGGER IF EXISTS trg_protect_changes_truncate ON changes;
        DROP TRIGGER IF EXISTS trg_protect_digests_immutability ON digests;
        DROP TRIGGER IF EXISTS trg_enforce_digest_publication ON digests;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claims_insert ON digest_claims;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claims_update ON digest_claims;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claims_delete ON digest_claims;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claims_truncate ON digest_claims;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claim_citations_insert ON digest_claim_citations;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claim_citations_update ON digest_claim_citations;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claim_citations_delete ON digest_claim_citations;
        DROP TRIGGER IF EXISTS trg_protect_published_digest_claim_citations_truncate ON digest_claim_citations;
        DROP FUNCTION IF EXISTS check_source_items_immutability();
        DROP FUNCTION IF EXISTS validate_fact_observed_at();
        DROP FUNCTION IF EXISTS validate_change_provenance();
        DROP FUNCTION IF EXISTS check_changes_immutability();
        DROP FUNCTION IF EXISTS check_digests_immutability();
        DROP FUNCTION IF EXISTS check_digest_publication_prerequisites();
        DROP FUNCTION IF EXISTS check_published_digest_claims_immutability();
        DROP FUNCTION IF EXISTS check_published_digest_claim_citations_immutability();
        DROP FUNCTION IF EXISTS reject_row_mutation();
        DROP FUNCTION IF EXISTS reject_table_truncate();
    """)
    Base.metadata.drop_all(bind=op.get_bind())
