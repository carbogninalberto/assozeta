package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CourseTagWrites struct{ db *sqlx.DB }

func NewCourseTagWrites(db *sqlx.DB) *CourseTagWrites { return &CourseTagWrites{db: db} }

type courseTagWriteTransaction struct{ *paymentCategoryTransaction }

func (r *CourseTagWrites) BeginCourseTagWrite(ctx context.Context) (domain.CourseTagWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseTagWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *courseTagWriteTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *courseTagWriteTransaction) TagOf(ctx context.Context, id, association string) (*string, bool, error) {
	var name string
	err := t.tx.GetContext(ctx, &name, `SELECT tag_name FROM application_coursetags WHERE tag_id=$1 AND sport_association_id=$2
 ORDER BY tag_id LIMIT 1 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	return &name, true, nil
}

func (t *courseTagWriteTransaction) LiveCourse(ctx context.Context, id, association string, group *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_course
 WHERE course_id=$1 AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid))`, id, association, group)
	return found, err
}

func courseTagAudit(id string, action int, changes map[string][2]string, association *string, path string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	entry := modelAuditEntry{Model: "coursetags", Key: id, Representation: "CourseTags object (" + id + ")", Action: action, Changes: changes,
		Context: audit, At: at}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	return entry
}

func (t *courseTagWriteTransaction) CreateTag(ctx context.Context, id, name, association string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_coursetags (tag_id,tag_name,sport_association_id,creation_date) VALUES ($1,$2,$3,$4)`,
		id, name, association, at); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_coursetags", "tag_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, courseTagAudit(id, 0, changes, &association, "direct", audit, at))
}

func (t *courseTagWriteTransaction) RenameTag(ctx context.Context, id, name, association string, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursetags SET tag_name=$2 WHERE tag_id=$1`, id, name); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseTagAudit(id, 1, changes, &association, "direct", audit, at))
}

func (t *courseTagWriteTransaction) DeleteTag(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	// The collector deletes the Course.tags through rows first.
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_course_tags WHERE coursetags_id=$1`, id); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_coursetags", "tag_id", id)
	if err != nil {
		return err
	}
	var association *string
	if value := changes["sport_association"][0]; value != "" && value != "None" {
		association = &value
	}
	return appendModelAudit(ctx, t.tx, courseTagAudit(id, 2, changes, association, "changes_json.sport_association", audit, at))
}

// LinkTag is course.tags.add(tag) / remove(tag): add keeps an existing link.
func (t *courseTagWriteTransaction) LinkTag(ctx context.Context, tag, course string, linked bool) error {
	if !linked {
		_, err := t.tx.ExecContext(ctx, `DELETE FROM application_course_tags WHERE course_id=$1 AND coursetags_id=$2`, course, tag)
		return err
	}
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_course_tags (course_id,coursetags_id) SELECT $1,$2
 WHERE NOT EXISTS (SELECT 1 FROM application_course_tags WHERE course_id=$1 AND coursetags_id=$2)`, course, tag)
	return err
}
