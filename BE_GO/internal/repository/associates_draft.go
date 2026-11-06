package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jmoiron/sqlx"
)

type AssociateDrafts struct{ db *sqlx.DB }

func NewAssociateDrafts(db *sqlx.DB) *AssociateDrafts { return &AssociateDrafts{db: db} }

var _ domain.AssociateDraftStore = (*AssociateDrafts)(nil)

type associateDraftTransaction struct{ *paymentCategoryTransaction }

func (r *AssociateDrafts) BeginAssociateDrafts(ctx context.Context) (domain.AssociateDraftTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &associateDraftTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *associateDraftTransaction) Association(ctx context.Context, owner string, impersonation *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, impersonation, false)
}

func (t *associateDraftTransaction) FirstAssociation(ctx context.Context, owner string, impersonation *string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY sport_association_id LIMIT 1`, owner, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *associateDraftTransaction) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	return t.FirstAssociation(ctx, actor, nil)
}

func (t *associateDraftTransaction) Drafts(ctx context.Context, association string) ([]domain.AssociateDraftRow, error) {
	rows := []struct {
		ID    string  `db:"id"`
		Data  *string `db:"data"`
		Valid *bool   `db:"valid"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT associate_import_draft_id::text AS id, data::text AS data, valid
 FROM application_associateimportdraft WHERE sport_association_id=$1`, association); err != nil {
		return nil, err
	}
	result := make([]domain.AssociateDraftRow, 0, len(rows))
	for _, row := range rows {
		item := domain.AssociateDraftRow{ID: row.ID, Valid: row.Valid}
		if row.Data != nil {
			item.Data = []byte(*row.Data)
		}
		result = append(result, item)
	}
	return result, nil
}

func (t *associateDraftTransaction) LatestStatus(ctx context.Context, association string, kind int) (*domain.AssociateDraftStatus, error) {
	var row struct {
		ID      string    `db:"id"`
		Doing   *bool     `db:"doing"`
		Created time.Time `db:"creation_date"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT associate_import_draft_status_id::text AS id, doing, creation_date
 FROM application_associateimportdraftstatus WHERE sport_association_id=$1 AND status_type=$2
 ORDER BY creation_date DESC LIMIT 1 FOR UPDATE`, association, kind)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.AssociateDraftStatus{ID: row.ID, Doing: row.Doing, Created: row.Created}, nil
}

func associateDraftBool(value *bool) string {
	switch {
	case value == nil:
		return "None"
	case *value:
		return "True"
	}
	return "False"
}

func (t *associateDraftTransaction) StopStatus(ctx context.Context, status domain.AssociateDraftStatus, association string,
	audit domain.LoginAuditContext, at time.Time) error {
	if status.Doing != nil && !*status.Doing {
		return nil // save() writes the same values: no auditlog entry
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_associateimportdraftstatus SET doing=false
 WHERE associate_import_draft_status_id=$1`, status.ID); err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associateimportdraftstatus", Key: status.ID,
		Representation: "AssociateImportDraftStatus object (" + status.ID + ")", Action: 1,
		Changes: map[string][2]string{"doing": {associateDraftBool(status.Doing), "False"}}, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *associateDraftTransaction) exists(ctx context.Context, query, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, query, id)
	return found, err
}

func (t *associateDraftTransaction) AssociationExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1)`, id)
}

func (t *associateDraftTransaction) FamilyExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT EXISTS(SELECT 1 FROM application_family WHERE family_id=$1)`, id)
}

func (t *associateDraftTransaction) CreateDraft(ctx context.Context, id, association string, data []byte, changes map[string][2]string, valid bool,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_associateimportdraft (associate_import_draft_id, sport_association_id, data, valid)
 VALUES ($1, $2, $3::text::jsonb, $4)`, id, association, string(data), valid); err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associateimportdraft", Key: id,
		Representation: "AssociateImportDraft object (" + id + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *associateDraftTransaction) Draft(ctx context.Context, id, association string) (*domain.AssociateDraftRow, error) {
	var row struct {
		Data  *string `db:"data"`
		Valid *bool   `db:"valid"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT data::text AS data, valid FROM application_associateimportdraft
 WHERE sport_association_id=$1 AND associate_import_draft_id=$2 FOR UPDATE`, association, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	result := &domain.AssociateDraftRow{ID: id, Valid: row.Valid}
	if row.Data != nil {
		result.Data = []byte(*row.Data)
	}
	return result, nil
}

// associateDraftStoredJSON is auditlog's rendering of the stored data
// (json.dumps(sort_keys=True) of the value read back, 'null' for NULL).
func associateDraftStoredJSON(data []byte) (string, error) {
	if data == nil {
		return "null", nil
	}
	parsed, err := dto.ParseProfileTablesJSON(data)
	if err != nil {
		return "", err
	}
	return string(dto.PythonJSONDumpsSorted(parsed)), nil
}

func (t *associateDraftTransaction) SaveDraft(ctx context.Context, id, association string, data, sorted []byte, valid bool,
	audit domain.LoginAuditContext, at time.Time) error {
	old, err := t.Draft(ctx, id, association)
	if err != nil {
		return err
	}
	if old == nil {
		return domain.ErrAssociateDraftNotFound
	}
	oldData, err := associateDraftStoredJSON(old.Data)
	if err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `UPDATE application_associateimportdraft SET sport_association_id=$2, data=$3::text::jsonb, valid=$4
 WHERE associate_import_draft_id=$1`, id, association, string(data), valid); err != nil {
		return err
	}
	changes := map[string][2]string{}
	if oldData != string(sorted) {
		changes["data"] = [2]string{oldData, string(sorted)}
	}
	if newValid := associateDraftBool(&valid); associateDraftBool(old.Valid) != newValid {
		changes["valid"] = [2]string{associateDraftBool(old.Valid), newValid}
	}
	if len(changes) == 0 {
		return nil
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associateimportdraft", Key: id,
		Representation: "AssociateImportDraft object (" + id + ")", Action: 1, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *associateDraftTransaction) Certificate(ctx context.Context, id string) (*domain.AssociateDraftCertificate, error) {
	var expiration *string
	err := t.tx.GetContext(ctx, &expiration, `SELECT expiration_date::text FROM application_medicalcertificate WHERE medical_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.AssociateDraftCertificate{ID: id, Expiration: expiration}, nil
}

func associateDraftActorPath(association *string) *string {
	if association == nil {
		return nil
	}
	path := "actor.sport_association"
	return &path
}

func (t *associateDraftTransaction) CreateCertificate(ctx context.Context, id, user string, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_medicalcertificate
 (medical_id, document_id, user_id, creation_date, expiration_date, competitive_medical_certificate, notes)
 VALUES ($1, NULL, $2, $3, NULL, false, NULL)`, id, user, at); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_medicalcertificate", "medical_id", id)
	if err != nil {
		return err
	}
	// MedicalCertificate has no subscription to resolve: the actor's association, if any.
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "medicalcertificate", Key: id,
		Representation: "MedicalCertificate object (" + id + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at})
}

func (t *associateDraftTransaction) SaveCertificateExpiration(ctx context.Context, certificate domain.AssociateDraftCertificate, expiration,
	rendered string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_medicalcertificate SET expiration_date=$2::date WHERE medical_id=$1`,
		certificate.ID, expiration); err != nil {
		return err
	}
	old := "None"
	if certificate.Expiration != nil {
		old = *certificate.Expiration
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "medicalcertificate", Key: certificate.ID,
		Representation: "MedicalCertificate object (" + certificate.ID + ")", Action: 1,
		Changes: map[string][2]string{"expiration_date": {old, rendered}}, Context: audit,
		AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at})
}

func (t *associateDraftTransaction) DeleteDrafts(ctx context.Context, association string, ids []string, audit domain.LoginAuditContext,
	at time.Time) (int, error) {
	found := []string{}
	if err := t.tx.SelectContext(ctx, &found, `SELECT associate_import_draft_id::text FROM application_associateimportdraft
 WHERE sport_association_id=$1 AND associate_import_draft_id=ANY($2::uuid[]) ORDER BY associate_import_draft_id FOR UPDATE`,
		association, ids); err != nil {
		return 0, err
	}
	path := "changes_json.sport_association"
	for _, id := range found {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_associateimportdraft", "associate_import_draft_id", id)
		if err != nil {
			return 0, err
		}
		if err = appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associateimportdraft", Key: id,
			Representation: "AssociateImportDraft object (" + id + ")", Action: 2, Changes: changes, Context: audit,
			AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
			return 0, err
		}
	}
	return len(found), nil
}
