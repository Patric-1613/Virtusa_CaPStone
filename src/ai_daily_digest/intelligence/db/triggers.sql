-- =============================================================================
-- PostgreSQL Triggers for Intelligence Persistence — ADR 0011 §4
-- =============================================================================

-- Shared Trigger Helper Functions
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

-- -----------------------------------------------------------------------------
-- Source Items and Document Snapshots Immutability Triggers — ADR 0002 §11
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_source_items_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'source_items.id is immutable';
    END IF;
    IF NEW.first_fetched_at IS DISTINCT FROM OLD.first_fetched_at THEN
        RAISE EXCEPTION 'source_items.first_fetched_at is immutable';
    END IF;
    IF NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key THEN
        RAISE EXCEPTION 'source_items.dedupe_key is immutable';
    END IF;
    IF NEW.source_id IS DISTINCT FROM OLD.source_id THEN
        RAISE EXCEPTION 'source_items.source_id is immutable';
    END IF;
    IF NEW.canonical_url IS DISTINCT FROM OLD.canonical_url THEN
        RAISE EXCEPTION 'source_items.canonical_url is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_source_items_immutability
BEFORE UPDATE ON source_items
FOR EACH ROW
EXECUTE FUNCTION check_source_items_immutability();

CREATE TRIGGER trg_protect_document_snapshots_immutable_update
BEFORE UPDATE ON document_snapshots
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();

CREATE TRIGGER trg_protect_document_snapshots_immutable_delete
BEFORE DELETE ON document_snapshots
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();

CREATE TRIGGER trg_protect_document_snapshots_immutable_truncate
BEFORE TRUNCATE ON document_snapshots
FOR EACH STATEMENT
EXECUTE FUNCTION reject_table_truncate();

-- -----------------------------------------------------------------------------
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
EXECUTE FUNCTION reject_table_truncate();
