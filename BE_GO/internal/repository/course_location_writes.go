package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CourseLocationWrites struct{ db *sqlx.DB }

func NewCourseLocationWrites(db *sqlx.DB) *CourseLocationWrites { return &CourseLocationWrites{db: db} }

type courseLocationWriteTransaction struct{ *paymentCategoryTransaction }

func (r *CourseLocationWrites) BeginCourseLocationWrite(ctx context.Context) (domain.CourseLocationWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseLocationWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *courseLocationWriteTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *courseLocationWriteTransaction) Location(ctx context.Context, id, association string) (*domain.CourseLocation, error) {
	row := struct {
		ID            string  `db:"course_location_id"`
		Title         *string `db:"title"`
		Description   *string `db:"description"`
		Address       *string `db:"address"`
		AssociationID *string `db:"sport_association_id"`
	}{}
	err := t.tx.GetContext(ctx, &row, `SELECT course_location_id, title, description, address, sport_association_id
 FROM application_courselocation WHERE course_location_id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.CourseLocation{ID: row.ID, Title: row.Title, Description: row.Description, Address: row.Address,
		AssociationID: row.AssociationID}, nil
}

func (t *courseLocationWriteTransaction) LocationIDTaken(ctx context.Context, id string, exclude *string) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_courselocation
 WHERE course_location_id=$1 AND ($2::uuid IS NULL OR course_location_id<>$2::uuid))`, id, exclude)
	return taken, err
}

func (t *courseLocationWriteTransaction) AssociationExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1)`, id)
	return exists, err
}

func courseLocationAudit(id string, action int, changes map[string][2]string, association *string, path string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	entry := modelAuditEntry{Model: "courselocation", Key: id, Representation: "CourseLocation object (" + id + ")", Action: action,
		Changes: changes, Context: audit, At: at}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	return entry
}

func (t *courseLocationWriteTransaction) CreateLocation(ctx context.Context, row domain.CourseLocation, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_courselocation (course_location_id,title,description,address,sport_association_id)
 VALUES ($1,$2,$3,$4,$5)`, row.ID, row.Title, row.Description, row.Address, row.AssociationID); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_courselocation", "course_location_id", row.ID)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, courseLocationAudit(row.ID, 0, changes, row.AssociationID, "direct", audit, at))
}

// SaveLocation is Model.save(): every field is written, auditlog records the
// changed ones only.
func (t *courseLocationWriteTransaction) SaveLocation(ctx context.Context, row domain.CourseLocation, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_courselocation SET title=$2, description=$3, address=$4, sport_association_id=$5
 WHERE course_location_id=$1`, row.ID, row.Title, row.Description, row.Address, row.AssociationID); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseLocationAudit(row.ID, 1, changes, row.AssociationID, "direct", audit, at))
}

func (t *courseLocationWriteTransaction) DeleteLocation(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	// The collector deletes the documents through rows first; documents stay.
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_courselocation_documents WHERE courselocation_id=$1`, id); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_courselocation", "course_location_id", id)
	if err != nil {
		return err
	}
	var association *string
	if value := changes["sport_association"][0]; value != "" && value != "None" {
		association = &value
	}
	return appendModelAudit(ctx, t.tx, courseLocationAudit(id, 2, changes, association, "changes_json.sport_association", audit, at))
}

func (t *courseLocationWriteTransaction) LocationDocuments(ctx context.Context, id string) (string, error) {
	var documents string
	err := t.tx.GetContext(ctx, &documents, `SELECT COALESCE((SELECT json_agg(json_build_object('document_id', d.document_id,
 'creation_date', d.creation_date, 'filepath', d.filepath, 'filename', d.filename, 'file_size_bytes', d.file_size_bytes, 'token', d.token))
  FROM application_courselocation_documents ld JOIN docmanager_document d ON d.document_id=ld.document_id
  WHERE ld.courselocation_id=$1), '[]'::json)::text`, id)
	return documents, err
}
