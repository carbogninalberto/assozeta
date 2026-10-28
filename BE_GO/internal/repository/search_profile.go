package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SearchProfiles struct{ db *sqlx.DB }

func NewSearchProfiles(db *sqlx.DB) *SearchProfiles { return &SearchProfiles{db: db} }

var _ domain.SearchProfileStore = (*SearchProfiles)(nil)

func (r *SearchProfiles) User(ctx context.Context, username string) (*domain.SearchProfileUser, error) {
	var row domain.SearchProfileUser
	// iexact is UPPER(column::text) = UPPER(value); first() orders by pk.
	err := r.db.GetContext(ctx, &row, `SELECT user_id, role, first_name, last_name, username, avatar_image, disable_account_creation,
 force_account_creation, online_payments, subscription_start_month, subscription_start_day FROM bakney_user
 WHERE NOT deleted AND UPPER(username::text)=UPPER($1) ORDER BY user_id LIMIT 1`, username)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

func (r *SearchProfiles) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

func (r *SearchProfiles) Module(ctx context.Context, association string) (domain.SearchProfileModule, error) {
	var row domain.SearchProfileModule
	err := r.db.GetContext(ctx, &row, `SELECT demand, regulation, additional_sections::text AS additional_sections, show_regulation_to_members,
 show_regulation_to_both, show_regulation_to_athletes, show_demand_to_members, show_demand_to_both, show_demand_to_athletes
 FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return row, err
}

// One query per pinned state: the course columns, its tags and locations
// (M2M, unordered) and the soft-delete manager's subscription count.
func (r *SearchProfiles) Courses(ctx context.Context, association string, pinned bool) ([]domain.SearchProfileCourse, error) {
	rows := []domain.SearchProfileCourse{}
	err := r.db.SelectContext(ctx, &rows, `SELECT c.course_id, c.sport_association_id, c.status_flag, c.course_type, c.creation_date, c.title,
 c.description, c.multi_payments, c.fee::text AS fee, c.events::text AS events, c.one_fee_payment, c.one_fee::text AS one_fee,
 c.google_calendar_id, c.google_background_color,
 COALESCE((SELECT json_agg(json_build_object('tag_id', t.tag_id, 'tag_name', t.tag_name)) FROM application_course_tags ct
  JOIN application_coursetags t ON t.tag_id=ct.coursetags_id WHERE ct.course_id=c.course_id), '[]'::json)::text AS tags,
 c.multiple_quotes::text AS multiple_quotes,
 COALESCE((SELECT json_agg(json_build_object('course_location_id', l.course_location_id, 'title', l.title, 'address', l.address))
  FROM application_course_locations cl JOIN application_courselocation l ON l.course_location_id=cl.courselocation_id
  WHERE cl.course_id=c.course_id), '[]'::json)::text AS locations,
 c.start_date, c.end_date, c.pinned, c.billed_duration_is_sport_season, c.billed_frequency, c.billed_from_subscription_date,
 c.billed_from_day_of_month, c.auto_renewal, c.deleted, c.group_id,
 (SELECT count(*) FROM application_coursesubscription s WHERE s.course_id=c.course_id AND NOT s.deleted) AS total_subscriptions
 FROM application_course c WHERE c.sport_association_id=$1 AND c.status_flag=2 AND c.pinned=$2 AND NOT c.deleted
 ORDER BY c.creation_date DESC`, association, pinned)
	return rows, err
}

func (r *SearchProfiles) Carnets(ctx context.Context, association string) ([]domain.CarnetListCarnet, error) {
	rows := []domain.CarnetListCarnet{}
	err := r.db.SelectContext(ctx, &rows, `SELECT carnet_id, title, description, lessons_number, creation_date, fee::text AS fee, public,
 meta::text AS meta FROM application_carnet WHERE sport_association_id=$1`, association)
	return rows, err
}

func (r *SearchProfiles) Tags(ctx context.Context, association string) ([]domain.SearchProfileTag, error) {
	rows := []domain.SearchProfileTag{}
	err := r.db.SelectContext(ctx, &rows, `SELECT tag_id, tag_name FROM application_coursetags WHERE sport_association_id=$1`, association)
	return rows, err
}

// OrderedTags is CourseTags.objects.filter(sport_association=...).order_by('tag_name').
func (r *SearchProfiles) OrderedTags(ctx context.Context, association string) ([]domain.SearchProfileTag, error) {
	rows := []domain.SearchProfileTag{}
	err := r.db.SelectContext(ctx, &rows, `SELECT tag_id, tag_name FROM application_coursetags WHERE sport_association_id=$1 ORDER BY tag_name`, association)
	return rows, err
}
