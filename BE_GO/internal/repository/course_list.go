package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CourseLists struct{ db *sqlx.DB }

func NewCourseLists(db *sqlx.DB) *CourseLists { return &CourseLists{db: db} }

// OwnerAssociation is scoped_queryset(SportAssociation.objects).get(user=owner):
// domain.ErrAssociationNotFound when there is none.
func (r *CourseLists) OwnerAssociation(ctx context.Context, owner string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, scope, false)
}

// SubscriptionExists is scoped_queryset(Subscription.objects).get(subscription_id=id)
// finding a row: the group-aware manager without a deleted filter.
func (r *CourseLists) SubscriptionExists(ctx context.Context, id string, group, scope *string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE subscription_id=$1
 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid))`, id, group, scope)
	return found, err
}

// BalanceStart is request.user's balance_sheet_start_day and _month.
func (r *CourseLists) BalanceStart(ctx context.Context, user string) (int64, int64, error) {
	var row struct {
		Day   int64 `db:"balance_sheet_start_day"`
		Month int64 `db:"balance_sheet_start_month"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT balance_sheet_start_day, balance_sheet_start_month FROM bakney_user WHERE user_id=$1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, 0, domain.ErrAccountNotFound
	}
	return row.Day, row.Month, err
}

// courseListFrom renders the queryset's FROM and WHERE like Django: one INNER
// JOIN of the tags through table for an OR filter (a course matching two ids
// is two rows) and one per id for the chained AND filters.
func courseListFrom(query domain.CourseListQuery) (string, []any) {
	args := []any{query.AssociationID, query.GroupID, query.Scope}
	arg := func(value any) string {
		args = append(args, value)
		return "$" + strconv.Itoa(len(args))
	}
	var joins, where strings.Builder
	where.WriteString(` WHERE c.sport_association_id=$1 AND NOT c.deleted AND ($2::uuid IS NULL OR c.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid)`)
	if query.ActiveOnly {
		where.WriteString(" AND c.status_flag=2")
	}
	if query.ExcludeSubscription != nil {
		// The view lists the subscription's live course subscriptions first;
		// their scope adds nothing: the subscription and the listed courses
		// already belong to the scoped association.
		where.WriteString(` AND NOT (c.course_id IN (SELECT cs.course_id FROM application_coursesubscription cs
 WHERE NOT cs.deleted AND cs.subscription_id=` + arg(*query.ExcludeSubscription) + `::uuid))`)
	}
	if query.Search != "" {
		pattern := arg("%" + strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(query.Search) + "%")
		where.WriteString(" AND (UPPER(c.title::text) LIKE UPPER(" + pattern + ") OR UPPER(c.description::text) LIKE UPPER(" + pattern + "))")
	}
	if len(query.Tags) > 0 {
		if query.TagsAnd {
			for index, tag := range query.Tags {
				alias := "t" + strconv.Itoa(index)
				joins.WriteString(" JOIN application_course_tags " + alias + " ON " + alias + ".course_id=c.course_id")
				where.WriteString(" AND " + alias + ".coursetags_id=" + arg(tag) + "::uuid")
			}
		} else {
			joins.WriteString(" JOIN application_course_tags t ON t.course_id=c.course_id")
			where.WriteString(" AND t.coursetags_id=ANY(" + arg(query.Tags) + "::uuid[])")
		}
	}
	if query.Status != nil {
		where.WriteString(" AND c.status_flag=" + arg(*query.Status))
	}
	return " FROM application_course c" + joins.String() + where.String(), args
}

func courseListOrder(query domain.CourseListQuery) string {
	switch {
	case query.Random:
		return " ORDER BY RANDOM()"
	case query.OrderColumn == "":
		return " ORDER BY c.creation_date DESC"
	case query.Descending:
		return " ORDER BY c." + query.OrderColumn + " DESC"
	}
	return " ORDER BY c." + query.OrderColumn + " ASC"
}

func (r *CourseLists) CountCourses(ctx context.Context, query domain.CourseListQuery) (int64, error) {
	if query.Empty {
		return 0, nil
	}
	from, args := courseListFrom(query)
	var count int64
	err := r.db.GetContext(ctx, &count, "SELECT COUNT(*)"+from, args...)
	return count, err
}

// Courses returns the CourseSerializer rows in the queryset's order; a
// negative limit reads them all.
func (r *CourseLists) Courses(ctx context.Context, query domain.CourseListQuery, limit, offset int64) ([]domain.CourseOverviewRow, error) {
	rows := []domain.CourseOverviewRow{}
	if query.Empty {
		return rows, nil
	}
	from, args := courseListFrom(query)
	statement := "SELECT " + courseSerializerColumns + from + courseListOrder(query)
	if limit >= 0 {
		statement += " LIMIT " + strconv.FormatInt(limit, 10) + " OFFSET " + strconv.FormatInt(offset, 10)
	}
	err := r.db.SelectContext(ctx, &rows, statement, args...)
	return rows, err
}

// CourseValues is values('course_id', 'title').
func (r *CourseLists) CourseValues(ctx context.Context, query domain.CourseListQuery) ([]domain.CourseListValue, error) {
	rows := []domain.CourseListValue{}
	if query.Empty {
		return rows, nil
	}
	from, args := courseListFrom(query)
	err := r.db.SelectContext(ctx, &rows, "SELECT c.course_id, c.title"+from+courseListOrder(query), args...)
	return rows, err
}

// Athletes is scoped_queryset(CourseSubscription.objects).filter(course_id__in=ids)
// .select_related('subscription__associate'): live rows, unordered, with LEFT
// JOINs for the nullable relations; a scoped session keeps the rows whose
// subscription and course belong to the association.
func (r *CourseLists) Athletes(ctx context.Context, ids []string, scope *string) ([]domain.CourseListAthlete, error) {
	rows := []domain.CourseListAthlete{}
	err := r.db.SelectContext(ctx, &rows, `SELECT cs.course_id, cs.paid, cs.creation_date, cs.subscription_id, s.associate_id,
 a.first_name, a.last_name, a.born_date::text AS born_date, a.sex
 FROM application_coursesubscription cs
 LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE NOT cs.deleted AND cs.course_id=ANY($1::uuid[]) AND ($2::uuid IS NULL OR (s.sport_association_id=$2::uuid
 AND EXISTS(SELECT 1 FROM application_course c WHERE c.course_id=cs.course_id AND c.sport_association_id=$2::uuid)))`, ids, scope)
	return rows, err
}

// ExportCourses evaluates the queryset current_view_course iterates (the
// whole filtered, sorted list).
func (r *CourseLists) ExportCourses(ctx context.Context, query domain.CourseListQuery) ([]domain.CourseListExportRow, error) {
	rows := []domain.CourseListExportRow{}
	if query.Empty {
		return rows, nil
	}
	from, args := courseListFrom(query)
	err := r.db.SelectContext(ctx, &rows, `SELECT c.course_id, c.title, c.course_type, c.status_flag, c.start_date, c.end_date,
 c.creation_date, c.pinned`+from+courseListOrder(query), args...)
	return rows, err
}

// ExportEnrolled is CourseSubscription.objects.filter(course_id=id).count().
func (r *CourseLists) ExportEnrolled(ctx context.Context, course string) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*) AS "__count" FROM "application_coursesubscription"
 WHERE (NOT "application_coursesubscription"."deleted" AND "application_coursesubscription"."course_id" = $1::uuid)`, course)
	return count, err
}

// ExportCourseTags is prefetch_related('tags') for the courses (distinct ids
// in first-seen order, as the In lookup binds them). The statement is the
// one Django sends, without ORDER BY: each course keeps the order the
// database returns (course-list.md, "Row order").
func (r *CourseLists) ExportCourseTags(ctx context.Context, ids []string) ([]domain.CourseListExportTag, error) {
	rows := []domain.CourseListExportTag{}
	if len(ids) == 0 {
		return rows, nil
	}
	err := r.db.SelectContext(ctx, &rows, `SELECT ("application_course_tags"."course_id") AS "course_id",
 "application_coursetags"."tag_id", "application_coursetags"."tag_name", "application_coursetags"."sport_association_id",
 "application_coursetags"."creation_date" FROM "application_coursetags" INNER JOIN "application_course_tags"
 ON ("application_coursetags"."tag_id" = "application_course_tags"."coursetags_id")
 WHERE "application_course_tags"."course_id" = ANY($1::uuid[])`, ids)
	return rows, err
}

// ExportCourseLocations is str(l.title) for l in course.locations.all(), in
// the order the database returns Django's statement (no ORDER BY).
func (r *CourseLists) ExportCourseLocations(ctx context.Context, course string) ([]*string, error) {
	rows := []struct {
		ID            string  `db:"course_location_id"`
		Title         *string `db:"title"`
		Description   *string `db:"description"`
		Address       *string `db:"address"`
		AssociationID *string `db:"sport_association_id"`
	}{}
	err := r.db.SelectContext(ctx, &rows, `SELECT "application_courselocation"."course_location_id", "application_courselocation"."title",
 "application_courselocation"."description", "application_courselocation"."address", "application_courselocation"."sport_association_id"
 FROM "application_courselocation" INNER JOIN "application_course_locations"
 ON ("application_courselocation"."course_location_id" = "application_course_locations"."courselocation_id")
 WHERE "application_course_locations"."course_id" = $1::uuid`, course)
	titles := make([]*string, 0, len(rows))
	for _, row := range rows {
		titles = append(titles, row.Title)
	}
	return titles, err
}

// ExportAssociationTags is CourseTags.objects.filter(sport_association=...):
// Django's statement without ORDER BY (course-list.md, "Row order").
func (r *CourseLists) ExportAssociationTags(ctx context.Context, association string) ([]domain.CourseListExportTag, error) {
	rows := []struct {
		ID            string    `db:"tag_id"`
		Name          string    `db:"tag_name"`
		AssociationID string    `db:"sport_association_id"`
		CreationDate  time.Time `db:"creation_date"`
	}{}
	err := r.db.SelectContext(ctx, &rows, `SELECT "application_coursetags"."tag_id", "application_coursetags"."tag_name",
 "application_coursetags"."sport_association_id", "application_coursetags"."creation_date" FROM "application_coursetags"
 WHERE "application_coursetags"."sport_association_id" = $1::uuid`, association)
	tags := make([]domain.CourseListExportTag, 0, len(rows))
	for _, row := range rows {
		tags = append(tags, domain.CourseListExportTag{ID: row.ID, Name: row.Name, AssociationID: row.AssociationID, CreationDate: row.CreationDate})
	}
	return tags, err
}

// AssociationDenomination is sport_association.denomination.
func (r *CourseLists) AssociationDenomination(ctx context.Context, association string) (*string, error) {
	var denomination *string
	err := r.db.GetContext(ctx, &denomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return denomination, err
}
