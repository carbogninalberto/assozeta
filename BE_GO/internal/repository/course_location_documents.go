package repository

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The document uploads of CourseLocationSerializer._handle_documents.

func (t *courseLocationWriteTransaction) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	rows := []string{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT sport_association_id FROM application_sportassociation
 WHERE NOT deleted AND user_id=$1 ORDER BY sport_association_id LIMIT 1`, actor); err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func (t *courseLocationWriteTransaction) CreateDocument(ctx context.Context, document domain.NewDocument, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1, $2, NULL, $3, NULL, $4)`, document.ID, document.Created, document.Filename, document.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "docmanager_document", "document_id", document.ID)
	if err != nil {
		return err
	}
	for name, rendered := range documentReverse {
		changes[name] = [2]string{"None", rendered}
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", documentAudit(document.ID, 0, changes, actorAssociation, audit, at))
}

func (t *courseLocationWriteTransaction) LinkDocument(ctx context.Context, location, document string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_courselocation_documents (courselocation_id, document_id) VALUES ($1, $2)
 ON CONFLICT DO NOTHING`, location, document)
	return err
}
