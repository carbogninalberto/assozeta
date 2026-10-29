package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CourseOverviews struct{ db *sqlx.DB }

func NewCourseOverviews(db *sqlx.DB) *CourseOverviews { return &CourseOverviews{db: db} }

// CourseOverview is Course.objects.filter(course_id=id,
// sport_association__user=owner).first(): live and group-aware, with the
// tags and locations prefetches (unordered).
func (r *CourseOverviews) CourseOverview(ctx context.Context, id, owner string, group *string) (*domain.CourseOverviewRow, error) {
	var row domain.CourseOverviewRow
	err := r.db.GetContext(ctx, &row, `SELECT c.course_id, c.status_flag, c.course_type, c.creation_date, c.title, c.description,
 c.multi_payments, c.fee::text AS fee, c.events::text AS events, c.one_fee_payment, c.one_fee::text AS one_fee, c.google_calendar_id,
 c.google_background_color, c.multiple_quotes::text AS multiple_quotes, c.start_date, c.end_date, c.pinned,
 c.billed_duration_is_sport_season, c.billed_frequency, c.billed_from_subscription_date, c.billed_from_day_of_month, c.auto_renewal,
 c.deleted, c.sport_association_id, c.group_id,
 COALESCE((SELECT json_agg(json_build_object('tag_id', t.tag_id, 'tag_name', t.tag_name)) FROM application_course_tags ct
   JOIN application_coursetags t ON t.tag_id=ct.coursetags_id WHERE ct.course_id=c.course_id), '[]'::json)::text AS tags,
 COALESCE((SELECT json_agg(json_build_object('course_location_id', l.course_location_id, 'title', l.title, 'address', l.address))
   FROM application_course_locations cl JOIN application_courselocation l ON l.course_location_id=cl.courselocation_id
   WHERE cl.course_id=c.course_id), '[]'::json)::text AS locations
 FROM application_course c JOIN application_sportassociation a ON a.sport_association_id=c.sport_association_id
 WHERE c.course_id=$1 AND a.user_id=$2 AND NOT c.deleted AND ($3::uuid IS NULL OR c.group_id=$3::uuid)
 ORDER BY c.course_id LIMIT 1`, id, owner, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}
