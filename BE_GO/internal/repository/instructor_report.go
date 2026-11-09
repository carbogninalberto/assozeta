package repository

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// InstructorReport reads what get_report reads.
type InstructorReport struct{ db *sqlx.DB }

func NewInstructorReport(db *sqlx.DB) *InstructorReport { return &InstructorReport{db: db} }

func (r *InstructorReport) OwnerAssociation(ctx context.Context, user string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, user, nil, false)
}

func (r *InstructorReport) Instructors(ctx context.Context, user string) ([]domain.InstructorReportInstructor, error) {
	rows := []domain.InstructorReportInstructor{}
	err := r.db.SelectContext(ctx, &rows, `SELECT instructor_id, first_name, last_name FROM application_instructor WHERE user_id=$1`, user)
	return rows, err
}

func (r *InstructorReport) Registries(ctx context.Context, association string) ([]domain.InstructorReportRegistry, error) {
	rows := []domain.InstructorReportRegistry{}
	err := r.db.SelectContext(ctx, &rows, `SELECT "application_attendanceregistry"."attendance_registry_id",
 "application_attendanceregistry"."events"::text AS "events", "application_course"."title" AS "course_title"
 FROM "application_attendanceregistry" INNER JOIN "application_course"
 ON ("application_attendanceregistry"."course_id" = "application_course"."course_id")
 WHERE "application_course"."sport_association_id" = $1`, association)
	return rows, err
}

func (r *InstructorReport) Days(ctx context.Context, registry string, from, to time.Time) ([]domain.InstructorReportDay, error) {
	rows := []domain.InstructorReportDay{}
	err := r.db.SelectContext(ctx, &rows, `SELECT title, date, attendees::text AS attendees, expected_absences::text AS expected_absences,
 associated_event::text AS associated_event FROM application_attendanceday
 WHERE attendance_registry_id = $1 AND date BETWEEN $2 AND $3 ORDER BY date ASC`, registry, from, to)
	return rows, err
}

func (r *InstructorReport) Names(ctx context.Context, ids []string) (map[string]string, error) {
	names := map[string]string{}
	if len(ids) == 0 {
		return names, nil
	}
	query, args, err := sqlx.In(`SELECT cs.course_subscription_id::text AS id,
 COALESCE(a.first_name, '') || ' ' || COALESCE(a.last_name, '') AS full_name
 FROM application_coursesubscription cs
 LEFT OUTER JOIN application_subscription s ON (cs.subscription_id = s.subscription_id)
 LEFT OUTER JOIN application_associate a ON (s.associate_id = a.associate_id)
 WHERE NOT cs.deleted AND cs.course_subscription_id::text IN (?)`, ids)
	if err != nil {
		return nil, err
	}
	rows := []struct {
		ID   string `db:"id"`
		Name string `db:"full_name"`
	}{}
	if err := r.db.SelectContext(ctx, &rows, r.db.Rebind(query), args...); err != nil {
		return nil, err
	}
	for _, row := range rows {
		names[row.ID] = row.Name
	}
	return names, nil
}
