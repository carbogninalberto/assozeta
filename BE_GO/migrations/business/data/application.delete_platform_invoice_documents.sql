CREATE TEMP TABLE retired_invoice_documents ON COMMIT DROP AS
SELECT DISTINCT document_id
FROM application_sportassociationinvoices
WHERE document_id IS NOT NULL;

-- Retain documents referenced elsewhere, including references through join tables.
DO $$
DECLARE
    reference record;
BEGIN
    FOR reference IN
        SELECT c.conrelid::regclass AS relation, a.attname AS column_name
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND c.conkey = ARRAY[a.attnum]
        WHERE c.contype = 'f'
          AND c.confrelid = 'docmanager_document'::regclass
          AND c.conrelid <> 'application_sportassociationinvoices'::regclass
    LOOP
        EXECUTE format(
            'DELETE FROM retired_invoice_documents d WHERE EXISTS (SELECT 1 FROM %s r WHERE r.%I = d.document_id)',
            reference.relation, reference.column_name
        );
    END LOOP;
END $$;

DELETE FROM application_sportassociationinvoices
WHERE document_id IN (SELECT document_id FROM retired_invoice_documents);
DELETE FROM docmanager_document
WHERE document_id IN (SELECT document_id FROM retired_invoice_documents);
