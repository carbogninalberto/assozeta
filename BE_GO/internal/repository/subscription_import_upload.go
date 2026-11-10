package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionImport struct{ db *sqlx.DB }

func NewSubscriptionImport(db *sqlx.DB) *SubscriptionImport { return &SubscriptionImport{db: db} }

var _ domain.SubscriptionImportStore = (*SubscriptionImport)(nil)

func (r *SubscriptionImport) BeginSubscriptionImport(ctx context.Context) (domain.SubscriptionImportTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted, ReadOnly: true})
	if err != nil {
		return nil, err
	}
	return &subscriptionImportTransaction{ownedTransaction: tx}, nil
}

type subscriptionImportTransaction struct{ *ownedTransaction }

func (t *subscriptionImportTransaction) Association(ctx context.Context, user string, impersonation *string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted
 AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY sport_association_id LIMIT 1`, user, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

func (t *subscriptionImportTransaction) DocumentExists(ctx context.Context, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM docmanager_document WHERE document_id=$1)`, id)
	return found, err
}

func (t *subscriptionImportTransaction) Document(ctx context.Context, id string) (*domain.SubscriptionImportDocument, error) {
	var row struct {
		Created  time.Time `db:"creation_date"`
		Filename string    `db:"filename"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT creation_date, filename FROM docmanager_document WHERE document_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &domain.SubscriptionImportDocument{Created: row.Created, Filename: row.Filename}, err
}

const (
	subscriptionImportStatusTable      = "application_associateimportdraftstatus"
	subscriptionImportStatusKey        = "associate_import_draft_status_id"
	subscriptionImportCertificateTable = "application_medicalcertificate"
)

// subscriptionImportDeleteStatus is one row of the status queryset's delete().
func subscriptionImportDeleteStatus(ctx context.Context, tx *sqlx.Tx, id, association string, audit domain.LoginAuditContext, at time.Time) error {
	changes, _, err := deleteModelWithAudit(ctx, tx, subscriptionImportStatusTable, subscriptionImportStatusKey, id)
	if err != nil {
		return err
	}
	path := "changes_json.sport_association"
	return appendModelAudit(ctx, tx, modelAuditEntry{Model: "associateimportdraftstatus", Key: id,
		Representation: "AssociateImportDraftStatus object (" + id + ")", Action: 2, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (r *SubscriptionImport) ImportRows(ctx context.Context, association, user string, actor *string, effects domain.SubscriptionImportEffects,
	audit domain.LoginAuditContext, at func() time.Time) error {
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer owned.Rollback()
	tx := owned.tx
	// A MedicalCertificate resolves to the actor's own association, if any.
	actorAssociation, actorPath, err := subscriptionImportActorAssociation(ctx, tx, actor)
	if err != nil {
		return err
	}
	if effects.Failed {
		effects.Certificates, effects.Drafts = nil, nil
	}
	// manage_import_status('start').
	var statuses []string
	if err = tx.SelectContext(ctx, &statuses, `SELECT `+subscriptionImportStatusKey+` FROM `+subscriptionImportStatusTable+`
 WHERE sport_association_id=$1 AND status_type=1 ORDER BY `+subscriptionImportStatusKey+` FOR UPDATE`, association); err != nil {
		return err
	}
	for _, id := range statuses {
		if err = subscriptionImportDeleteStatus(ctx, tx, id, association, audit, at()); err != nil {
			return err
		}
	}
	created := at()
	if _, err = tx.ExecContext(ctx, `INSERT INTO `+subscriptionImportStatusTable+`
 (associate_import_draft_status_id, sport_association_id, doing, status_type, creation_date) VALUES ($1, $2, true, 1, $3)`,
		effects.StatusID, association, created); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, tx, subscriptionImportStatusTable, subscriptionImportStatusKey, effects.StatusID)
	if err != nil {
		return err
	}
	direct := "direct"
	if err = appendModelAudit(ctx, tx, modelAuditEntry{Model: "associateimportdraftstatus", Key: effects.StatusID,
		Representation: "AssociateImportDraftStatus object (" + effects.StatusID + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &direct, At: created}); err != nil {
		return err
	}
	for _, certificate := range effects.Certificates {
		created = at()
		if _, err = tx.ExecContext(ctx, `INSERT INTO `+subscriptionImportCertificateTable+`
 (medical_id, document_id, user_id, creation_date, expiration_date, competitive_medical_certificate, notes)
 VALUES ($1, NULL, $2, $3, NULL, false, NULL)`, certificate.ID, user, created); err != nil {
			return err
		}
		if changes, err = createdModelChanges(ctx, tx, subscriptionImportCertificateTable, "medical_id", certificate.ID); err != nil {
			return err
		}
		entry := modelAuditEntry{Model: "medicalcertificate", Key: certificate.ID,
			Representation: "MedicalCertificate object (" + certificate.ID + ")", Action: 0, Changes: changes, Context: audit,
			AssociationID: actorAssociation, ResolutionPath: actorPath, At: created}
		if err = appendModelAudit(ctx, tx, entry); err != nil {
			return err
		}
		if certificate.Expiration == nil {
			// The text was no date: medical_certificate.delete().
			if entry.Changes, _, err = deleteModelWithAudit(ctx, tx, subscriptionImportCertificateTable, "medical_id", certificate.ID); err != nil {
				return err
			}
			entry.Action, entry.At = 2, at()
		} else {
			// make_aware(...) saved into the DateField; auditlog renders the aware datetime.
			day := certificate.Expiration.Format("2006-01-02")
			if _, err = tx.ExecContext(ctx, `UPDATE `+subscriptionImportCertificateTable+` SET expiration_date=$2::date WHERE medical_id=$1`,
				certificate.ID, day); err != nil {
				return err
			}
			entry.Changes = map[string][2]string{"expiration_date": {"None", day + " 00:00:00+00:00"}}
			entry.Action, entry.At = 1, at()
		}
		if err = appendModelAudit(ctx, tx, entry); err != nil {
			return err
		}
	}
	// bulk_create: no signals, no audit entries.
	for _, draft := range effects.Drafts {
		if _, err = tx.ExecContext(ctx, `INSERT INTO application_associateimportdraft (associate_import_draft_id, sport_association_id, data, valid)
 VALUES ($1, $2, $3::text::jsonb, $4)`, draft.ID, association, string(draft.JSON), draft.Valid); err != nil {
			return err
		}
	}
	// manage_import_status('end').
	if err = subscriptionImportDeleteStatus(ctx, tx, effects.StatusID, association, audit, at()); err != nil {
		return err
	}
	if !effects.Failed {
		// remove_upload: document.delete().
		if err = subscriptionImportDeleteDocument(ctx, tx, effects.DocumentID, actorAssociation, audit, at()); err != nil {
			return err
		}
	}
	return owned.Commit()
}

// subscriptionImportActorAssociation is the association auditlog resolves
// for a row without one of its own: the actor's, if any.
func subscriptionImportActorAssociation(ctx context.Context, tx *sqlx.Tx, actor *string) (*string, *string, error) {
	if actor == nil {
		return nil, nil, nil
	}
	var id string
	err := tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted
 ORDER BY sport_association_id LIMIT 1`, *actor)
	switch {
	case err == nil:
		path := "actor.sport_association"
		return &id, &path, nil
	case errors.Is(err, sql.ErrNoRows):
		return nil, nil, nil
	}
	return nil, nil, err
}

// subscriptionImportDeleteDocument is Document.delete() of an import upload,
// which nothing references; a missing row is no write.
func subscriptionImportDeleteDocument(ctx context.Context, tx *sqlx.Tx, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	var found bool
	if err := tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM docmanager_document WHERE document_id=$1 FOR UPDATE)`, id); err != nil {
		return err
	}
	if !found {
		return nil
	}
	changes, _, err := deleteModelWithAuditReverse(ctx, tx, "docmanager_document", "document_id", id, documentReverse)
	if err != nil {
		return err
	}
	return appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(id, 2, changes, actorAssociation, audit, at))
}

func (r *SubscriptionImport) RemoveDocuments(ctx context.Context, ids []string, actor *string, audit domain.LoginAuditContext,
	at func() time.Time) error {
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer owned.Rollback()
	actorAssociation, _, err := subscriptionImportActorAssociation(ctx, owned.tx, actor)
	if err != nil {
		return err
	}
	for _, id := range ids {
		if err = subscriptionImportDeleteDocument(ctx, owned.tx, id, actorAssociation, audit, at()); err != nil {
			return err
		}
	}
	return owned.Commit()
}
