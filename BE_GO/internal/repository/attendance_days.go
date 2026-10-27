package repository

import (
	"context"
	"database/sql"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AttendanceDays struct{ db *sqlx.DB }

func NewAttendanceDays(db *sqlx.DB) *AttendanceDays { return &AttendanceDays{db: db} }

var _ domain.AttendanceDayStore = (*AttendanceDays)(nil)

func (r *AttendanceDays) BeginAttendanceDay(ctx context.Context) (domain.AttendanceDayTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &attendanceDayTransaction{archiveWriteTransaction: &archiveWriteTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

type attendanceDayTransaction struct{ *archiveWriteTransaction }

// OwnedAttendanceDay mirrors scoped_queryset(AttendanceDay).filter(attendance_day_id=uid,
// attendance_registry__course__sport_association__user=request.user).first().
func (t *attendanceDayTransaction) OwnedAttendanceDay(ctx context.Context, id, owner string, scope *string) error {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_attendanceday d
 JOIN application_attendanceregistry r ON r.attendance_registry_id=d.attendance_registry_id
 JOIN application_course c ON c.course_id=r.course_id
 JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE d.attendance_day_id=$1 AND sa.user_id=$2 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid))`, id, owner, scope)
	if err == nil && !exists {
		return domain.ErrAttendanceDayNotFound
	}
	return err
}

// DeleteAttendanceDay deletes the day with auditlog's entry; the deleted row
// no longer resolves, so the index falls back to the actor's association.
func (t *attendanceDayTransaction) DeleteAttendanceDay(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_attendanceday", "attendance_day_id", id)
	if err != nil {
		return err
	}
	event := modelAuditEntry{Model: "attendanceday", Key: id, Representation: "AttendanceDay object (" + id + ")", Action: 2,
		Changes: changes, Context: audit, At: at}
	if actorAssociation != nil {
		path := "actor.sport_association"
		event.AssociationID, event.ResolutionPath = actorAssociation, &path
	}
	return appendModelAudit(ctx, t.tx, event)
}
