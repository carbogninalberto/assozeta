package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CourseStateWrites struct{ db *sqlx.DB }

func NewCourseStateWrites(db *sqlx.DB) *CourseStateWrites { return &CourseStateWrites{db: db} }

type courseStateWriteTransaction struct{ *paymentCategoryTransaction }

func (r *CourseStateWrites) BeginCourseStateWrite(ctx context.Context) (domain.CourseStateWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseStateWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *courseStateWriteTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *courseStateWriteTransaction) LiveCourse(ctx context.Context, id string, group *string) (*domain.CourseState, error) {
	row := struct {
		StatusFlag    int     `db:"status_flag"`
		Pinned        bool    `db:"pinned"`
		AssociationID *string `db:"sport_association_id"`
	}{}
	err := t.tx.GetContext(ctx, &row, `SELECT status_flag, pinned, sport_association_id FROM application_course
 WHERE course_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY course_id LIMIT 1 FOR UPDATE`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.CourseState{StatusFlag: row.StatusFlag, Pinned: row.Pinned, AssociationID: row.AssociationID}, nil
}

func (t *courseStateWriteTransaction) SaveCourse(ctx context.Context, id string, state domain.CourseState, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_course SET status_flag=$2, pinned=$3 WHERE course_id=$1`,
		id, state.StatusFlag, state.Pinned); err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "course", Key: id, Representation: "Course object (" + id + ")", Action: 1, Changes: changes,
		Context: audit, At: at}
	if state.AssociationID != nil {
		path := "direct"
		entry.AssociationID, entry.ResolutionPath = state.AssociationID, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}
