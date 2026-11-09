package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type AutoMarkAttendance struct{ db *sqlx.DB }

func NewAutoMarkAttendance(db *sqlx.DB) *AutoMarkAttendance { return &AutoMarkAttendance{db: db} }

var _ domain.AutoMarkAttendanceStore = (*AutoMarkAttendance)(nil)

func (r *AutoMarkAttendance) BeginAutoMarkAttendance(ctx context.Context, job domain.Job) (domain.AutoMarkAttendanceTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &autoMarkTransaction{ownedTransaction: tx}, nil
}

type autoMarkTransaction struct{ *ownedTransaction }

// The task's querysets have no ordering; these statements keep the same
// filters and no ORDER BY, so PostgreSQL returns rows in the same order it
// returns them to Django. .first() orders by primary key.

func (t *autoMarkTransaction) Owners(ctx context.Context) ([]uuid.UUID, error) {
	var ids []uuid.UUID
	err := t.tx.SelectContext(ctx, &ids, `SELECT user_id FROM bakney_user
 WHERE (NOT deleted AND auto_mark_attendance AND role=1)`)
	return ids, err
}

func (t *autoMarkTransaction) FirstAssociation(ctx context.Context, user uuid.UUID) (*uuid.UUID, error) {
	return t.firstID(ctx, `SELECT sport_association_id FROM application_sportassociation
 WHERE (NOT deleted AND user_id=$1) ORDER BY sport_association_id ASC LIMIT 1`, user)
}

func (t *autoMarkTransaction) ActiveCourses(ctx context.Context, association uuid.UUID) ([]domain.AutoMarkCourse, error) {
	var rows []struct {
		ID          uuid.UUID `db:"course_id"`
		Title       *string   `db:"title"`
		Association uuid.UUID `db:"sport_association_id"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT course_id,title,sport_association_id FROM application_course
 WHERE (NOT deleted AND sport_association_id=$1 AND status_flag=2)`, association)
	courses := make([]domain.AutoMarkCourse, len(rows))
	for i, row := range rows {
		courses[i] = domain.AutoMarkCourse{ID: row.ID, Title: row.Title, AssociationID: row.Association}
	}
	return courses, err
}

func (t *autoMarkTransaction) FirstRegistry(ctx context.Context, course uuid.UUID) (*uuid.UUID, error) {
	return t.firstID(ctx, `SELECT attendance_registry_id FROM application_attendanceregistry
 WHERE course_id=$1 ORDER BY attendance_registry_id ASC LIMIT 1`, course)
}

func (t *autoMarkTransaction) RangeDays(ctx context.Context, registry uuid.UUID, from, to time.Time) ([]domain.AutoMarkDay, error) {
	var rows []struct {
		ID        uuid.UUID `db:"attendance_day_id"`
		Title     *string   `db:"title"`
		Date      time.Time `db:"date"`
		Attendees *string   `db:"attendees"`
		Absences  *string   `db:"expected_absences"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT attendance_day_id,title,date,attendees::text AS attendees,
 expected_absences::text AS expected_absences FROM application_attendanceday
 WHERE (attendance_registry_id=$1 AND NOT auto_marked AND date BETWEEN $2 AND $3)`, registry, from, to)
	days := make([]domain.AutoMarkDay, len(rows))
	for i, row := range rows {
		days[i] = domain.AutoMarkDay{ID: row.ID, Title: row.Title, Date: row.Date, Attendees: row.Attendees, ExpectedAbsences: row.Absences}
	}
	return days, err
}

func (t *autoMarkTransaction) CourseSubscriptionIDs(ctx context.Context, course uuid.UUID) ([]uuid.UUID, error) {
	var ids []uuid.UUID
	err := t.tx.SelectContext(ctx, &ids, `SELECT course_subscription_id FROM application_coursesubscription
 WHERE (NOT deleted AND course_id=$1)`, course)
	return ids, err
}

func (t *autoMarkTransaction) CourseSubscription(ctx context.Context, id uuid.UUID) (*domain.AutoMarkCourseSubscription, error) {
	var row domain.AutoMarkCourseSubscription
	err := t.tx.QueryRowxContext(ctx, `SELECT course_subscription_id,subscription_id FROM application_coursesubscription
 WHERE (NOT deleted AND course_subscription_id=$1) ORDER BY course_subscription_id ASC LIMIT 1`, id).Scan(&row.ID, &row.SubscriptionID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// Carnets is the task's CarnetSubscription query: linked to the course
// subscription or to none, same subscription (IS NULL for none), enabled,
// newest first.
func (t *autoMarkTransaction) Carnets(ctx context.Context, courseSubscription uuid.UUID, subscription *uuid.UUID) ([]domain.AutoMarkCarnet, error) {
	var rows []struct {
		ID           uuid.UUID  `db:"carnet_subscription_id"`
		Subscription *uuid.UUID `db:"subscription_id"`
		Payment      *uuid.UUID `db:"payment_id"`
		Meta         *string    `db:"meta"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT h.carnet_subscription_id,h.subscription_id,h.payment_id,h.meta::text AS meta
 FROM application_carnetsubscription h LEFT OUTER JOIN application_carnetsubscription_course_subscription l
 ON (h.carnet_subscription_id=l.carnetsubscription_id)
 WHERE ((l.coursesubscription_id=$1 OR l.coursesubscription_id IS NULL) AND NOT h.disabled
  AND h.subscription_id IS NOT DISTINCT FROM $2::uuid)
 ORDER BY h.creation_date DESC`, courseSubscription, subscription)
	carnets := make([]domain.AutoMarkCarnet, len(rows))
	for i, row := range rows {
		carnets[i] = domain.AutoMarkCarnet{ID: row.ID, SubscriptionID: row.Subscription, PaymentID: row.Payment, Meta: row.Meta}
	}
	return carnets, err
}

// PaymentPaid follows carnet.payment, which uses the base manager.
func (t *autoMarkTransaction) PaymentPaid(ctx context.Context, id uuid.UUID) (bool, error) {
	var paid bool
	err := t.tx.GetContext(ctx, &paid, `SELECT paid FROM application_payment WHERE payment_id=$1`, id)
	return paid, err
}

// SaveCarnetMeta mirrors carnet.save(): auditlog's pre_save entry (only meta
// changes), indexed by carnet_subscription_resolver through the subscription's
// association when there is one, then the UPDATE.
func (t *autoMarkTransaction) SaveCarnetMeta(ctx context.Context, carnet domain.AutoMarkCarnet, meta string, changes map[string][2]string, at time.Time) error {
	if len(changes) != 0 {
		entry := modelAuditEntry{Model: "carnetsubscription", Key: carnet.ID.String(),
			Representation: "CarnetSubscription object (" + carnet.ID.String() + ")", Action: 1, Changes: changes, At: at}
		if carnet.SubscriptionID != nil {
			var association *string
			err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_subscription WHERE subscription_id=$1`, *carnet.SubscriptionID)
			if err != nil && !errors.Is(err, sql.ErrNoRows) {
				return err
			}
			if association != nil {
				path := "subscription.sport_association"
				entry.AssociationID, entry.ResolutionPath = association, &path
			}
		}
		if err := appendModelAudit(ctx, t.tx, entry); err != nil {
			return err
		}
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_carnetsubscription SET meta=$2::jsonb WHERE carnet_subscription_id=$1`, carnet.ID, meta)
	return err
}

// SaveDay mirrors attendance_day.save(): the audit entry indexed through the
// course's association, then attendees, expected_absences and auto_marked.
func (t *autoMarkTransaction) SaveDay(ctx context.Context, day domain.AutoMarkDay, association uuid.UUID, attendees, absences string, changes map[string][2]string, at time.Time) error {
	if len(changes) != 0 {
		id, path := association.String(), "attendance_registry.course.sport_association"
		if err := appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "attendanceday", Key: day.ID.String(),
			Representation: "AttendanceDay object (" + day.ID.String() + ")", Action: 1, Changes: changes,
			AssociationID: &id, ResolutionPath: &path, At: at}); err != nil {
			return err
		}
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_attendanceday SET attendees=$2::jsonb,expected_absences=$3::jsonb,auto_marked=true
 WHERE attendance_day_id=$1`, day.ID, attendees, absences)
	return err
}

func (t *autoMarkTransaction) firstID(ctx context.Context, query string, argument uuid.UUID) (*uuid.UUID, error) {
	var id uuid.UUID
	err := t.tx.GetContext(ctx, &id, query, argument)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *autoMarkTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *autoMarkTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *autoMarkTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
