package repository

import (
	"context"
	"database/sql"
	"errors"
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

var _ domain.AttendanceMarkStore = (*AttendanceDays)(nil)

func (r *AttendanceDays) BeginAttendanceMark(ctx context.Context) (domain.AttendanceMarkTransaction, error) {
	tx, err := r.BeginAttendanceDay(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*attendanceDayTransaction), nil
}

func (t *attendanceDayTransaction) PrimaryAssociation(ctx context.Context) (*string, error) {
	var id *string
	err := t.tx.GetContext(ctx, &id, `SELECT primary_association_id::text FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return id, err
}

func (t *attendanceDayTransaction) DayForMark(ctx context.Context, id string, association *string, owner string, scope *string) (domain.AttendanceDayLists, error) {
	var row domain.AttendanceDayLists
	err := t.tx.GetContext(ctx, &row, `SELECT c.sport_association_id AS course_association_id, c.course_id,
 d.attendees::text AS attendees, d.expected_absences::text AS expected_absences
 FROM application_attendanceday d JOIN application_attendanceregistry r ON r.attendance_registry_id=d.attendance_registry_id
 JOIN application_course c ON c.course_id=r.course_id JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE d.attendance_day_id=$1 AND ($2::uuid IS NOT NULL AND c.sport_association_id=$2::uuid OR $2::uuid IS NULL AND sa.user_id=$3)
 AND ($4::uuid IS NULL OR c.sport_association_id=$4::uuid) FOR UPDATE OF d`, id, association, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAttendanceDayNotFound
	}
	return row, err
}

// CourseSubscriptionVisible mirrors scoped_queryset(CourseSubscription).filter(pk=..., course=...,
// subscription__sport_association=...) with the impersonation boundary and, for an
// athlete, both the scoped family visibility and the view's own athlete filter.
// Subscription.objects has no soft-delete filter; Associate.objects excludes deleted rows.
func (t *attendanceDayTransaction) CourseSubscriptionVisible(ctx context.Context, id, course, association string, scope *string, athlete *string) (bool, error) {
	var visible bool
	err := t.tx.GetContext(ctx, &visible, `WITH tax AS (
  SELECT a.tax_code FROM application_subscription s JOIN application_associate a ON a.associate_id=s.associate_id
  WHERE s.user_id=$5::uuid AND s.sport_association_id=$3::uuid AND a.tax_code IS NOT NULL AND a.tax_code<>''),
 tax_scoped AS (SELECT a.tax_code FROM application_subscription s JOIN application_associate a ON a.associate_id=s.associate_id
  WHERE s.user_id=$5::uuid AND s.sport_association_id=$4::uuid AND a.tax_code IS NOT NULL AND a.tax_code<>''),
 members AS (SELECT associate_id FROM application_associate WHERE sport_association_id=$4::uuid AND NOT deleted
  AND (user_id=$5::uuid OR tax_code IN (SELECT tax_code FROM tax_scoped)))
 SELECT EXISTS(SELECT 1 FROM application_coursesubscription cs JOIN application_subscription s ON s.subscription_id=cs.subscription_id
  JOIN application_course c ON c.course_id=cs.course_id
  LEFT JOIN application_associate sa ON sa.associate_id=s.associate_id
  WHERE NOT cs.deleted AND cs.course_subscription_id=$1::uuid AND cs.course_id=$2::uuid AND s.sport_association_id=$3::uuid
  AND ($4::uuid IS NULL OR (s.sport_association_id=$4::uuid AND c.sport_association_id=$4::uuid
       AND ($5::uuid IS NULL OR s.subscription_id IN (SELECT subscription_id FROM application_subscription
            WHERE sport_association_id=$4::uuid AND (user_id=$5::uuid OR associate_id IN (SELECT associate_id FROM members))))))
  AND ($5::uuid IS NULL OR s.user_id=$5::uuid OR sa.user_id=$5::uuid OR sa.tax_code IN (SELECT tax_code FROM tax)))`,
		id, course, association, scope, athlete)
	return visible, err
}

func (t *attendanceDayTransaction) SetAttendanceLists(ctx context.Context, id string, attendees, absences *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_attendanceday SET attendees=$2::jsonb, expected_absences=$3::jsonb
 WHERE attendance_day_id=$1`, id, attendees, absences)
	return err
}

func (t *attendanceDayTransaction) AppendAttendanceAudit(ctx context.Context, event domain.AttendanceListsAudit) error {
	path := "attendance_registry.course.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "attendanceday", Key: event.DayID,
		Representation: "AttendanceDay object (" + event.DayID + ")", Action: 1, Changes: event.Changes, Context: event.Context,
		AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At})
}
