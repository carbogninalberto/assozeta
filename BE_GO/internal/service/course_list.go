package service

import (
	"context"
	"errors"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrCourseListServer is an exception course_list does not handle (a 500):
	// a malformed or missing subscription_id, a malformed tag id or status,
	// an order_by name the model does not have, an invalid balance start.
	ErrCourseListServer = errors.New("course list server error")
	// ErrCourseListUnported refuses an ordering Django accepts lazily but Go
	// does not port (relations, reverse relations, transforms); it is raised
	// where Django compiles the ordering, after the pagination checks.
	ErrCourseListUnported = refusal.New("course list ordering not ported")
	// ErrCourseListExport is a POST without all=1: the handler reads
	// request.data and refuses current_view_course (see course-list.md).
	ErrCourseListExport = errors.New("course list export")
	// ErrCourseListExportUnported refuses generating the PDF or XLSX file.
	ErrCourseListExportUnported = refusal.New("course list export file generation not ported")
)

type courseListStore interface {
	OwnerAssociation(ctx context.Context, owner string, scope *string) (string, error)
	SubscriptionExists(ctx context.Context, id string, group, scope *string) (bool, error)
	BalanceStart(ctx context.Context, user string) (int64, int64, error)
	CountCourses(ctx context.Context, query domain.CourseListQuery) (int64, error)
	Courses(ctx context.Context, query domain.CourseListQuery, limit, offset int64) ([]domain.CourseOverviewRow, error)
	CourseValues(ctx context.Context, query domain.CourseListQuery) ([]domain.CourseListValue, error)
	Athletes(ctx context.Context, ids []string, scope *string) ([]domain.CourseListAthlete, error)
}

type CourseLists struct {
	store courseListStore
	now   func() time.Time
}

func NewCourseLists(store courseListStore, now func() time.Time) *CourseLists {
	return &CourseLists{store: store, now: now}
}

// CourseListRequest carries what course_list reads from the request.
type CourseListRequest struct {
	Query       domain.RequestObject
	GroupHeader string
	Method      string
}

// courseListColumns are the Course field names and attnames order_by
// resolves to one local column ('pk' is course_id); the relations to
// SportAssociation and Group have no Meta.ordering, so they order by the
// foreign key column.
var courseListColumns = map[string]string{
	"course_id": "course_id", "sport_association": "sport_association_id", "sport_association_id": "sport_association_id",
	"status_flag": "status_flag", "course_type": "course_type", "creation_date": "creation_date", "title": "title",
	"description": "description", "multi_payments": "multi_payments", "fee": "fee", "events": "events",
	"one_fee_payment": "one_fee_payment", "one_fee": "one_fee", "google_calendar_id": "google_calendar_id",
	"google_background_color": "google_background_color", "multiple_quotes": "multiple_quotes", "start_date": "start_date",
	"end_date": "end_date", "pinned": "pinned", "billed_duration_is_sport_season": "billed_duration_is_sport_season",
	"billed_frequency": "billed_frequency", "billed_from_subscription_date": "billed_from_subscription_date",
	"billed_from_day_of_month": "billed_from_day_of_month", "auto_renewal": "auto_renewal", "deleted": "deleted",
	"group": "group_id", "group_id": "group_id",
}

// courseListRelations are the other names Options.get_field resolves on
// Course (M2M and reverse relations): order_by accepts them and paths
// through any known name, and only the compiled query uses or rejects them.
var courseListRelations = map[string]bool{"tags": true, "locations": true, "coursesubscription": true, "attendanceregistry": true}

func courseListText(query domain.RequestObject, name string) string {
	value, _ := query.Lookup(name)
	text, _ := value.(string)
	return text
}

func courseListScope(identity domain.ResolvedIdentity) *string {
	if identity.ImpersonationAssociationID == "" {
		return nil
	}
	value := identity.ImpersonationAssociationID
	return &value
}

// courseListOrdering is order_by(name): '?' is random; one leading '-' is
// descending; an unknown first name is a FieldError at order_by() (a 500
// now); anything else is resolved lazily and not ported (unported=true).
func courseListOrdering(query *domain.CourseListQuery, name string) (unported bool, err error) {
	if name == "?" {
		query.Random = true
		return false, nil
	}
	field, descending := strings.CutPrefix(name, "-")
	parts := strings.Split(field, "__")
	first := parts[0]
	if first == "pk" {
		first = "course_id"
	}
	column, local := courseListColumns[first]
	switch {
	case local && len(parts) == 1:
		query.OrderColumn, query.Descending = column, descending
		return false, nil
	case local || courseListRelations[first]:
		return true, nil
	}
	return false, ErrCourseListServer
}

// List follows course_list.
func (s *CourseLists) List(ctx context.Context, identity domain.ResolvedIdentity, request CourseListRequest) (map[string]any, error) {
	scope, group := courseListScope(identity), supplierGroup(request.GroupHeader)
	if identity.ScopeUser.Role == domain.RoleAthlete {
		raw, present := request.Query.Lookup("sport_association_id")
		if !present {
			return nil, domain.ErrCourseListMissingAssociation
		}
		text, _ := raw.(string)
		id, err := paymentInfoParseUUID(text)
		if err != nil {
			return nil, err
		}
		rows, err := s.store.Courses(ctx, domain.CourseListQuery{AssociationID: id.String(), Scope: scope, GroupID: group, ActiveOnly: true}, -1, 0)
		if err != nil {
			return nil, err
		}
		return map[string]any{"data": courseListSerialized(rows)}, nil
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return nil, err
	}
	query := domain.CourseListQuery{AssociationID: association, Scope: scope, GroupID: group}
	if raw := courseListText(request.Query, "subscription_id"); raw != "" {
		id, err := paymentInfoParseUUID(raw)
		if err != nil {
			return nil, ErrCourseListServer
		}
		subscription := id.String()
		found, err := s.store.SubscriptionExists(ctx, subscription, group, scope)
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, ErrCourseListServer
		}
		query.ExcludeSubscription = &subscription
	}
	query.Search = courseListText(request.Query, "query[generalSearch]")
	if tags := courseListText(request.Query, "query[tags]"); tags != "" {
		for _, raw := range strings.Split(tags, ",") {
			id, err := paymentInfoParseUUID(raw)
			if err != nil {
				return nil, ErrCourseListServer
			}
			query.Tags = append(query.Tags, id.String())
		}
		query.TagsAnd = courseListText(request.Query, "query[tags_and]") == "1"
	}
	if raw := courseListText(request.Query, "query[status_flag]"); raw != "" {
		number, ok := dto.PythonIntText(raw)
		if !ok {
			return nil, ErrCourseListServer
		}
		// PositiveSmallIntegerField's range; outside it the lookup is EmptyResultSet.
		if number.Cmp(big.NewInt(0)) < 0 || number.Cmp(big.NewInt(32767)) > 0 {
			query.Empty = true
		} else {
			value := number.Int64()
			query.Status = &value
		}
	}
	unported := false
	field, direction := courseListText(request.Query, "sort[field]"), courseListText(request.Query, "sort[sort]")
	if field != "" && direction != "" {
		if direction != "asc" {
			field = "-" + field
		}
		if unported, err = courseListOrdering(&query, field); err != nil {
			return nil, err
		}
	}
	if courseListText(request.Query, "all") == "1" {
		if unported {
			return nil, ErrCourseListUnported
		}
		if courseListText(request.Query, "optimized") == "1" {
			values, err := s.store.CourseValues(ctx, query)
			if err != nil {
				return nil, err
			}
			data := make([]map[string]any, 0, len(values))
			for _, value := range values {
				data = append(data, map[string]any{"course_id": value.ID, "title": value.Title})
			}
			return map[string]any{"data": data}, nil
		}
		rows, err := s.store.Courses(ctx, query, -1, 0)
		if err != nil {
			return nil, err
		}
		return map[string]any{"data": courseListSerialized(rows)}, nil
	}
	if request.Method == "POST" {
		return nil, ErrCourseListExport
	}
	return s.page(ctx, identity, query, request.Query, unported)
}

// page is KTDatatablePagination, then current_year and athletes per course.
func (s *CourseLists) page(ctx context.Context, identity domain.ResolvedIdentity, query domain.CourseListQuery,
	parameters domain.RequestObject, unported bool) (map[string]any, error) {
	count, err := s.store.CountCourses(ctx, query)
	if err != nil {
		return nil, err
	}
	size, sizePresent := parameters.Lookup("pagination[perpage]")
	number, numberPresent := parameters.Lookup("pagination[page]")
	page, err := dto.DRFPageNumber(number, numberPresent, count, dto.DRFPageSize(size, sizePresent, 10))
	if err != nil {
		return nil, err
	}
	if unported {
		return nil, ErrCourseListUnported
	}
	rows, err := s.store.Courses(ctx, query, page.Size, page.Offset())
	if err != nil {
		return nil, err
	}
	day, month, err := s.store.BalanceStart(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	from, to, ok := balanceSheetRange(s.now(), day, month)
	if !ok {
		return nil, ErrCourseListServer
	}
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		ids = append(ids, row.ID)
	}
	athletes, err := s.store.Athletes(ctx, ids, courseListScope(identity))
	if err != nil {
		return nil, err
	}
	byCourse := map[string][]domain.CourseListAthlete{}
	for _, athlete := range athletes {
		byCourse[athlete.CourseID] = append(byCourse[athlete.CourseID], athlete)
	}
	data := make(map[string]any, len(rows))
	for index, row := range rows {
		course := courseSerializerData(row)
		created := row.CreationDate.UTC()
		midnight := time.Date(created.Year(), created.Month(), created.Day(), 0, 0, 0, 0, time.UTC)
		course["current_year"] = 0
		if !midnight.Before(from) && !midnight.After(to) {
			course["current_year"] = 1
		}
		course["athletes"] = courseListAthletes(byCourse[row.ID])
		data[strconv.Itoa(index)] = course
	}
	return map[string]any{"data": data, "meta": map[string]any{"total": page.Count, "page": page.Number, "pages": page.Pages,
		"perpage": page.Size, "rowIds": ids}}, nil
}

// courseListAthletes renders one course's athletes keyed by index. A
// subscription without an associate raises AttributeError inside the view's
// try: that entry keeps paid and creation_date and the course's list stops.
func courseListAthletes(rows []domain.CourseListAthlete) map[string]any {
	athletes := make(map[string]any, len(rows))
	for index, row := range rows {
		entry := map[string]any{"paid": row.Paid, "creation_date": dto.DRFUTCDateTime(row.CreationDate)}
		athletes[strconv.Itoa(index)] = entry
		if row.SubscriptionID == nil {
			continue
		}
		if row.AssociateID == nil {
			break
		}
		entry["subscription"] = map[string]any{"subscription_id": *row.SubscriptionID, "associate": map[string]any{
			"first_name": row.FirstName, "last_name": row.LastName, "born_date": row.BornDate, "sex": row.Sex}}
	}
	return athletes
}

func courseListSerialized(rows []domain.CourseOverviewRow) []map[string]any {
	data := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		data = append(data, courseSerializerData(row))
	}
	return data
}
