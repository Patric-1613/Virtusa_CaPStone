"""Intelligence persistence foundation migration — ADR 0011.
# pylint: skip-file

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERS_SQL = """-- =============================================================================
-- PostgreSQL Triggers for Intelligence Persistence — ADR 0011 §4
-- =============================================================================

CREATE OR REPLACE FUNCTION reject_row_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: updates and deletes are prohibited', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION reject_table_truncate()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: truncate is prohibited', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

-- Extracted Facts Immutability and Provenance Triggers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION validate_fact_observed_at()
RETURNS TRIGGER AS $$
DECLARE
    snapshot_fetched_at TIMESTAMPTZ;
BEGIN
    SELECT fetched_at INTO snapshot_fetched_at
    FROM document_snapshots
    WHERE id = NEW.snapshot_id;

    IF snapshot_fetched_at IS NULL THEN
        RAISE EXCEPTION 'Referenced document snapshot % does not exist', NEW.snapshot_id;
    END IF;

    IF NEW.observed_at IS DISTINCT FROM snapshot_fetched_at THEN
        RAISE EXCEPTION 'extracted_facts.observed_at (%) does not match document_snapshots.fetched_at (%)',
            NEW.observed_at, snapshot_fetched_at;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_extracted_fact_observed_at
BEFORE INSERT ON extracted_facts
FOR EACH ROW
EXECUTE FUNCTION validate_fact_observed_at();

CREATE TRIGGER trg_protect_extracted_facts_immutable_update
BEFORE UPDATE ON extracted_facts
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();

CREATE TRIGGER trg_protect_extracted_facts_immutable_delete
BEFORE DELETE ON extracted_facts
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();

CREATE TRIGGER trg_protect_extracted_facts_immutable_truncate
BEFORE TRUNCATE ON extracted_facts
FOR EACH STATEMENT
EXECUTE FUNCTION reject_table_truncate();

-- -----------------------------------------------------------------------------
-- Changes Immutability and Provenance Triggers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION validate_change_provenance()
RETURNS TRIGGER AS $$
DECLARE
    current_fetched_at TIMESTAMPTZ;
    prev_fetched_at TIMESTAMPTZ;
BEGIN
    SELECT fetched_at INTO current_fetched_at
    FROM document_snapshots
    WHERE id = NEW.current_snapshot_id;

    IF current_fetched_at IS NULL THEN
        RAISE EXCEPTION 'Current snapshot % does not exist', NEW.current_snapshot_id;
    END IF;

    IF NEW.current_observed_at IS DISTINCT FROM current_fetched_at THEN
        RAISE EXCEPTION 'changes.current_observed_at (%) does not match document_snapshots.fetched_at (%)',
            NEW.current_observed_at, current_fetched_at;
    END IF;

    IF NEW.previous_snapshot_id IS NOT NULL THEN
        SELECT fetched_at INTO prev_fetched_at
        FROM document_snapshots
        WHERE id = NEW.previous_snapshot_id;

        IF prev_fetched_at IS NULL THEN
            RAISE EXCEPTION 'Previous snapshot % does not exist', NEW.previous_snapshot_id;
        END IF;

        IF NEW.previous_observed_at IS DISTINCT FROM prev_fetched_at THEN
            RAISE EXCEPTION 'changes.previous_observed_at (%) does not match document_snapshots.fetched_at (%)',
                NEW.previous_observed_at, prev_fetched_at;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_change_provenance
BEFORE INSERT ON changes
FOR EACH ROW
EXECUTE FUNCTION validate_change_provenance();

CREATE OR REPLACE FUNCTION check_changes_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR
       NEW.detected_at IS DISTINCT FROM OLD.detected_at OR
       NEW.change_set_id IS DISTINCT FROM OLD.change_set_id OR
       NEW.position IS DISTINCT FROM OLD.position OR
       NEW.company_key IS DISTINCT FROM OLD.company_key OR
       NEW.product_key IS DISTINCT FROM OLD.product_key OR
       NEW.field IS DISTINCT FROM OLD.field OR
       NEW.change_type IS DISTINCT FROM OLD.change_type OR
       NEW.confidence IS DISTINCT FROM OLD.confidence OR
       NEW.previous_value IS DISTINCT FROM OLD.previous_value OR
       NEW.previous_observed_at IS DISTINCT FROM OLD.previous_observed_at OR
       NEW.previous_snapshot_id IS DISTINCT FROM OLD.previous_snapshot_id OR
       NEW.current_value IS DISTINCT FROM OLD.current_value OR
       NEW.current_observed_at IS DISTINCT FROM OLD.current_observed_at OR
       NEW.current_snapshot_id IS DISTINCT FROM OLD.current_snapshot_id OR
       NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Cannot update immutable columns on changes (only review_status may be updated)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_changes_immutability
BEFORE UPDATE ON changes
FOR EACH ROW
EXECUTE FUNCTION check_changes_immutability();

CREATE TRIGGER trg_protect_changes_delete
BEFORE DELETE ON changes
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();

CREATE TRIGGER trg_protect_changes_truncate
BEFORE TRUNCATE ON changes
FOR EACH STATEMENT
EXECUTE FUNCTION reject_table_truncate();

-- -----------------------------------------------------------------------------
-- Digests Immutability and Publication Gate Triggers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_digests_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'Cannot update immutable column digests.id';
    END IF;
    IF NEW.digest_date IS DISTINCT FROM OLD.digest_date THEN
        RAISE EXCEPTION 'Cannot update immutable column digests.digest_date';
    END IF;
    IF OLD.status = 'published' AND NEW.title IS DISTINCT FROM OLD.title THEN
        RAISE EXCEPTION 'Cannot update title of an already published digest';
    END IF;
    IF OLD.status = 'published' AND NEW.status IS DISTINCT FROM 'published' THEN
        RAISE EXCEPTION 'Cannot unpublish an already published digest';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_digests_immutability
BEFORE UPDATE ON digests
FOR EACH ROW
EXECUTE FUNCTION check_digests_immutability();

CREATE OR REPLACE FUNCTION check_digest_publication_prerequisites()
RETURNS TRIGGER AS $$
DECLARE
    unsupported_count INTEGER;
    claim_count INTEGER;
    uncited_count INTEGER;
BEGIN
    IF (TG_OP = 'INSERT' AND NEW.status = 'published') OR
       (TG_OP = 'UPDATE' AND NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published') THEN

        SELECT COUNT(*) INTO claim_count
        FROM digest_claims
        WHERE digest_id = NEW.id;

        IF claim_count = 0 THEN
            RAISE EXCEPTION 'Cannot publish digest %: digest has no claims', NEW.id;
        END IF;

        SELECT COUNT(*) INTO unsupported_count
        FROM digest_claims
        WHERE digest_id = NEW.id AND validation_status != 'supported';

        IF unsupported_count > 0 THEN
            RAISE EXCEPTION 'Cannot publish digest %: contains % unsupported claims', NEW.id, unsupported_count;
        END IF;

        SELECT COUNT(*) INTO uncited_count
        FROM digest_claims dc
        WHERE dc.digest_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM digest_claim_citations dcc WHERE dcc.claim_id = dc.id
          );

        IF uncited_count > 0 THEN
            RAISE EXCEPTION 'Cannot publish digest %: contains % claims without citations', NEW.id, uncited_count;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_digest_publication
BEFORE INSERT OR UPDATE ON digests
FOR EACH ROW
EXECUTE FUNCTION check_digest_publication_prerequisites();

-- -----------------------------------------------------------------------------
-- Digest Claims Child Locking and Immutability Triggers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_published_digest_claims_immutability()
RETURNS TRIGGER AS $$
DECLARE
    parent_status TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO parent_status FROM digests WHERE id = NEW.digest_id FOR KEY SHARE;
        IF parent_status = 'published' THEN
            RAISE EXCEPTION 'Cannot insert claims into an already published digest (%)', NEW.digest_id;
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT status INTO parent_status FROM digests WHERE id = OLD.digest_id FOR KEY SHARE;
        IF parent_status = 'published' THEN
            RAISE EXCEPTION 'Cannot delete claims from an already published digest (%)', OLD.digest_id;
        END IF;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        SELECT status INTO parent_status FROM digests WHERE id = OLD.digest_id FOR KEY SHARE;
        IF parent_status = 'published' THEN
            RAISE EXCEPTION 'Cannot reassign or modify claims of an already published digest (%)', OLD.digest_id;
        END IF;
        IF NEW.digest_id IS DISTINCT FROM OLD.digest_id THEN
            SELECT status INTO parent_status FROM digests WHERE id = NEW.digest_id FOR KEY SHARE;
            IF parent_status = 'published' THEN
                RAISE EXCEPTION 'Cannot reassign claims into an already published digest (%)', NEW.digest_id;
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_published_digest_claims_insert
BEFORE INSERT ON digest_claims
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claims_immutability();

CREATE TRIGGER trg_protect_published_digest_claims_update
BEFORE UPDATE ON digest_claims
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claims_immutability();

CREATE TRIGGER trg_protect_published_digest_claims_delete
BEFORE DELETE ON digest_claims
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claims_immutability();

CREATE TRIGGER trg_protect_published_digest_claims_truncate
BEFORE TRUNCATE ON digest_claims
FOR EACH STATEMENT
EXECUTE FUNCTION reject_table_truncate();

-- -----------------------------------------------------------------------------
-- Digest Claim Citations Child Locking and Immutability Triggers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_published_digest_claim_citations_immutability()
RETURNS TRIGGER AS $$
DECLARE
    parent_status TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT d.status INTO parent_status
        FROM digests d
        JOIN digest_claims dc ON dc.digest_id = d.id
        WHERE dc.id = NEW.claim_id
        FOR KEY SHARE;
        IF parent_status = 'published' THEN
            RAISE EXCEPTION 'Cannot insert citations for an already published digest';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT d.status INTO parent_status
        FROM digests d
        JOIN digest_claims dc ON dc.digest_id = d.id
        WHERE dc.id = OLD.claim_id
        FOR KEY SHARE;
        IF parent_status = 'published' THEN
            RAISE EXCEPTION 'Cannot delete citations from an already published digest';
        END IF;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        SELECT d.status INTO parent_status
        FROM digests d
        JOIN digest_claims dc ON dc.digest_id = d.id
        WHERE dc.id = OLD.claim_id
        FOR KEY SHARE;
        IF parent_status = 'published' THEN
            RAISE EXCEPTION 'Cannot reassign or modify citations of an already published digest';
        END IF;
        IF NEW.claim_id IS DISTINCT FROM OLD.claim_id THEN
            SELECT d.status INTO parent_status
            FROM digests d
            JOIN digest_claims dc ON dc.digest_id = d.id
            WHERE dc.id = NEW.claim_id
            FOR KEY SHARE;
            IF parent_status = 'published' THEN
                RAISE EXCEPTION 'Cannot reassign citations into an already published digest';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_published_digest_claim_citations_insert
BEFORE INSERT ON digest_claim_citations
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claim_citations_immutability();

CREATE TRIGGER trg_protect_published_digest_claim_citations_update
BEFORE UPDATE ON digest_claim_citations
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claim_citations_immutability();

CREATE TRIGGER trg_protect_published_digest_claim_citations_delete
BEFORE DELETE ON digest_claim_citations
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claim_citations_immutability();

CREATE TRIGGER trg_protect_published_digest_claim_citations_truncate
BEFORE TRUNCATE ON digest_claim_citations
FOR EACH STATEMENT
EXECUTE FUNCTION reject_table_truncate();"""


def upgrade() -> None:
    # 1. subjects
    op.create_table(
        "subjects",
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("company_key", "product_key", name="pk_subjects"),
    )

    # 2. extracted_facts
    op.create_table(
        "extracted_facts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("disclosure_status", sa.String(length=32), nullable=False),
        sa.Column("extraction_method", sa.String(length=64), nullable=False),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("quoted_span", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="chk_extracted_facts_confidence",
        ),
        sa.CheckConstraint(
            "extraction_version >= 1", name="chk_extracted_facts_extraction_version"
        ),
        sa.CheckConstraint(
            "disclosure_status IN ('disclosed', 'not_disclosed')",
            name="chk_extracted_facts_disclosure_status",
        ),
        sa.CheckConstraint(
            "extraction_method IN ('deterministic', 'llm_structured_output')",
            name="chk_extracted_facts_extraction_method",
        ),
        sa.ForeignKeyConstraint(
            ["company_key", "product_key"],
            ["subjects.company_key", "subjects.product_key"],
            name="fk_extracted_facts_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["document_snapshots.id"],
            name="fk_extracted_facts_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extracted_facts"),
        sa.UniqueConstraint(
            "id",
            "company_key",
            "product_key",
            "field",
            "snapshot_id",
            "observed_at",
            "extraction_version",
            name="uq_extracted_facts_composite_identity",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "company_key",
            "product_key",
            "field",
            "extraction_version",
            name="uq_extracted_facts_attempt",
        ),
    )
    op.create_index("idx_extracted_facts_snapshot_id", "extracted_facts", ["snapshot_id"])
    op.create_index(
        "idx_extracted_facts_subject_field_chronology",
        "extracted_facts",
        [
            "company_key",
            "product_key",
            "field",
            "observed_at",
            "snapshot_id",
            "extraction_version",
            "id",
        ],
    )

    # 6. current_facts
    op.create_table(
        "current_facts",
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("fact_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("extraction_version >= 1", name="chk_current_facts_extraction_version"),
        sa.ForeignKeyConstraint(
            ["company_key", "product_key"],
            ["subjects.company_key", "subjects.product_key"],
            name="fk_current_facts_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "fact_id",
                "company_key",
                "product_key",
                "field",
                "snapshot_id",
                "observed_at",
                "extraction_version",
            ],
            [
                "extracted_facts.id",
                "extracted_facts.company_key",
                "extracted_facts.product_key",
                "extracted_facts.field",
                "extracted_facts.snapshot_id",
                "extracted_facts.observed_at",
                "extracted_facts.extraction_version",
            ],
            name="fk_current_facts_extracted_fact_composite",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("company_key", "product_key", "field", name="pk_current_facts"),
    )

    # 7. change_sets
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("review_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_key", "product_key"],
            ["subjects.company_key", "subjects.product_key"],
            name="fk_change_sets_subject",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_change_sets"),
        sa.UniqueConstraint(
            "id",
            "company_key",
            "product_key",
            name="uq_change_sets_id_subject",
        ),
    )

    # 8. changes
    op.create_table(
        "changes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("change_set_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        # change_type is deliberately an OPEN string (ADR 0009) — no CHECK/length cap here,
        # unlike the closed enum-backed columns above.
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("previous_observed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("previous_snapshot_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("current_observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("current_snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="chk_changes_confidence"),
        sa.CheckConstraint("position >= 0", name="chk_changes_position"),
        sa.ForeignKeyConstraint(
            ["change_set_id", "company_key", "product_key"],
            ["change_sets.id", "change_sets.company_key", "change_sets.product_key"],
            name="fk_changes_change_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_key", "product_key"],
            ["subjects.company_key", "subjects.product_key"],
            name="fk_changes_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_snapshot_id"],
            ["document_snapshots.id"],
            name="fk_changes_current_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_snapshot_id"],
            ["document_snapshots.id"],
            name="fk_changes_previous_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_changes"),
        sa.UniqueConstraint("change_set_id", "position", name="uq_changes_change_set_position"),
    )
    op.create_index("idx_changes_pagination", "changes", ["detected_at", "id"])
    op.create_index("idx_changes_subject_field", "changes", ["company_key", "product_key", "field"])
    op.create_index("idx_changes_change_set_id", "changes", ["change_set_id"])
    op.create_index("idx_changes_previous_snapshot_id", "changes", ["previous_snapshot_id"])
    op.create_index("idx_changes_current_snapshot_id", "changes", ["current_snapshot_id"])

    # 9. digests
    op.create_table(
        "digests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'review', 'published')",
            name="chk_digests_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_digests"),
    )
    op.create_index(
        "idx_digests_pagination",
        "digests",
        ["digest_date", "id"],
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "uq_digests_one_published_per_date",
        "digests",
        ["digest_date"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    # 10. digest_claims
    op.create_table(
        "digest_claims",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("digest_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "validation_status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="chk_digest_claims_position"),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'supported', 'unsupported')",
            name="chk_digest_claims_validation_status",
        ),
        sa.ForeignKeyConstraint(
            ["digest_id"],
            ["digests.id"],
            name="fk_digest_claims_digest_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_digest_claims"),
        sa.UniqueConstraint("digest_id", "position", name="uq_digest_claims_digest_position"),
    )
    op.create_index("idx_digest_claims_digest_id", "digest_claims", ["digest_id"])

    # 11. digest_claim_citations
    op.create_table(
        "digest_claim_citations",
        sa.Column("claim_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="chk_digest_claim_citations_position"),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["digest_claims.id"],
            name="fk_digest_claim_citations_claim_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["document_snapshots.id"],
            name="fk_digest_claim_citations_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("claim_id", "snapshot_id", name="pk_digest_claim_citations"),
        sa.UniqueConstraint("claim_id", "position", name="uq_digest_claim_citations_position"),
    )
    op.create_index(
        "idx_digest_claim_citations_snapshot_id", "digest_claim_citations", ["snapshot_id"]
    )

    # Apply database triggers and functions
    op.execute(TRIGGERS_SQL)


def downgrade() -> None:
    """Explicitly drop immutability triggers and functions, then drop all intelligence tables in reverse dependency order."""
    op.execute("""
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

    # Drop intelligence tables in reverse dependency order
    op.drop_table("digest_claim_citations")
    op.drop_table("digest_claims")
    op.drop_table("digests")
    op.drop_table("changes")
    op.drop_table("change_sets")
    op.drop_table("current_facts")
    op.drop_table("extracted_facts")
    op.drop_table("subjects")
