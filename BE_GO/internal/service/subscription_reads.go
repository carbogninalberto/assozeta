package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"sort"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// SubscriptionReads ports subscription_list, subscription_list_all,
// subscription_list_archived, subscription_info and subscription_calendar.
type SubscriptionReads struct {
	store   domain.SubscriptionReadsStore
	courses domain.CourseSubscriptionStore
	now     func() time.Time
	// athletes enables the athlete list; tasks the info print dispatch.
	athletes *subscriptionReadsAthlete
	tasks    celery.Publisher
}

func NewSubscriptionReads(store domain.SubscriptionReadsStore, courses domain.CourseSubscriptionStore, now func() time.Time) *SubscriptionReads {
	return &SubscriptionReads{store: store, courses: courses, now: now}
}

// SubscriptionReadsRequest is the parsed request of one of the views.
type SubscriptionReadsRequest struct {
	Identity    domain.ResolvedIdentity
	Query       domain.RequestObject
	GroupHeader string
	Method      string
	// Authorization is request.headers.get('authorization').
	Authorization *string
}

func (s *SubscriptionReads) today() time.Time {
	now := s.now().UTC()
	return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
}

func subscriptionReadsTruthy(query domain.RequestObject, name string) string {
	text, _ := paymentReadsParam(query, name)
	return text
}

// subscriptionReadsScopedAthlete is an association-scoped session of an
// athlete: scoped_queryset()'s family boundary on Subscription is not ported.
func subscriptionReadsScopedAthlete(identity domain.ResolvedIdentity) bool {
	return identity.ScopeUser.Role == domain.RoleAthlete && paymentReadsScope(identity) != nil
}

func (s *SubscriptionReads) association(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	id, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentReadsScope(identity))
	if err != nil {
		return "", err
	}
	if id == nil {
		return "", domain.ErrSubscriptionReadsServer // SportAssociation.DoesNotExist
	}
	return *id, nil
}

func subscriptionReadsUUID(raw string) (string, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", domain.ErrSubscriptionReadsServer // UUIDField.to_python's ValidationError
	}
	return id.String(), nil
}

// subscriptionReadsInt is IntegerField.get_prep_value's int().
func subscriptionReadsInt(raw string) (*big.Int, error) {
	value, ok := dto.PythonIntText(raw)
	if !ok {
		return nil, domain.ErrSubscriptionReadsServer
	}
	return value, nil
}

// subscriptionReadsYearsAgo is (datetime.now() - relativedelta(years=n)).date().
func (s *SubscriptionReads) subscriptionReadsYearsAgo(raw string) (time.Time, error) {
	value, err := subscriptionReadsInt(raw)
	if err != nil {
		return time.Time{}, err
	}
	today := s.today()
	year := new(big.Int).Sub(big.NewInt(int64(today.Year())), value)
	if !year.IsInt64() || year.Int64() < 1 || year.Int64() > 9999 {
		return time.Time{}, domain.ErrSubscriptionReadsServer // year out of range
	}
	y := int(year.Int64())
	last := time.Date(y, today.Month()+1, 0, 0, 0, 0, 0, time.UTC).Day()
	return time.Date(y, today.Month(), min(today.Day(), last), 0, 0, 0, 0, time.UTC), nil
}

// subscriptionReadsRelations are Subscription's relations and reverse
// relations: order_by() accepts them and their paths, and Django resolves
// them only when compiling the rows.
var subscriptionReadsRelations = map[string]bool{"sport_association": true, "associate": true, "supplier": true, "instructor": true,
	"user": true, "medical": true, "document_pdf": true, "payment": true, "group": true, "tags": true, "medicalappointments": true,
	"subscriptionmembership": true, "soldproducts": true, "subscriptiontoken": true, "subscriptionfile": true, "subscriptiontransfer": true,
	"coursesubscription": true, "campsandretreatssubscription": true, "carnetsubscription": true}

var subscriptionReadsFields = map[string]string{"pk": "subscription_id", "subscription_id": "subscription_id", "custom_data": "custom_data",
	"additional_fields": "additional_fields", "type": "type", "role": "role", "acceptance_date": "acceptance_date",
	"resignation_date": "resignation_date", "status_flag": "status_flag", "creation_date": "creation_date", "start_date": "start_date",
	"end_date": "end_date", "signature_url": "signature_url", "signature_storage_key": "signature_storage_key", "archived": "archived",
	"draft": "draft", "trial": "trial", "meta": "meta", "subscription_number": "subscription_number", "subscription_type": "subscription_type",
	"deleted": "deleted", "competitive": "competitive", "notes": "notes", "sport_association_id": "sport_association_id",
	"associate_id": "associate_id", "supplier_id": "supplier_id", "instructor_id": "instructor_id", "user_id": "user_id",
	"medical_id": "medical_id", "document_pdf_id": "document_pdf_id", "payment_id": "payment_id", "group_id": "group_id"}

// errSubscriptionReadsLazyOrder marks an ordering Django resolves when
// compiling the rows (after the count and the page checks).
var errSubscriptionReadsLazyOrder = refusal.New("subscription ordering not ported")

// subscriptionReadsOrder is order_by(f"{'-' if desc else ”}{field}"): one
// leading '-' is stripped, then the name must resolve (FieldError, 500).
func subscriptionReadsOrder(field string, descending bool, annotations map[string]bool) ([]domain.SubscriptionReadsOrder, error) {
	if descending {
		field = "-" + field
	}
	if field == "?" {
		return nil, errSubscriptionReadsLazyOrder
	}
	name, desc := strings.CutPrefix(field, "-")
	if column, ok := subscriptionReadsFields[name]; ok {
		return []domain.SubscriptionReadsOrder{{Column: column, Descending: desc}}, nil
	}
	first, _, _ := strings.Cut(name, "__")
	if _, ok := subscriptionReadsFields[first]; ok || subscriptionReadsRelations[first] || annotations[name] {
		return nil, errSubscriptionReadsLazyOrder
	}
	return nil, domain.ErrSubscriptionReadsServer
}

// subscriptionReadsSmart is smart_search(queryset, term) for Subscription.
func (s *SubscriptionReads) subscriptionReadsSmart(filter *domain.SubscriptionReadsFilter, term string) error {
	search, err := personaSearch(term, s.now().UTC().Year()%100)
	if err != nil {
		return domain.ErrSubscriptionReadsNotPorted
	}
	filter.Smart = search
	return nil
}

func subscriptionReadsDMY(raw string) string {
	if date, ok := dto.PythonStrptimeDMY(raw); ok {
		return date
	}
	return raw
}

// page counts, checks the page, refuses a lazy ordering, then renders the rows.
func (s *SubscriptionReads) page(ctx context.Context, filter domain.SubscriptionReadsFilter, query domain.RequestObject, lazy bool,
	render func([]string) ([]any, error)) (any, error) {
	count, err := s.store.CountSubscriptions(ctx, filter)
	if err != nil {
		return nil, err
	}
	number, size, pages, limit, offset, err := paymentReadsPage(count, query)
	if err != nil {
		return nil, err
	}
	if lazy {
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	ids := []string{}
	if limit > 0 {
		if ids, err = s.store.Subscriptions(ctx, filter, limit, offset); err != nil {
			return nil, err
		}
	}
	items, err := render(ids)
	if err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "data", Value: subscriptionReadsIndexed(items)},
		{Name: "meta", Value: paymentReadsMeta(count, number, size, pages, ids)}}, nil
}

// subscriptionReadsIndexed is {idx: row for idx, row in enumerate(rows)}.
func subscriptionReadsIndexed(items []any) domain.RequestObject {
	data := domain.RequestObject{}
	for i, item := range items {
		data = append(data, domain.RequestField{Name: big.NewInt(int64(i)).String(), Value: item})
	}
	return data
}

func (s *SubscriptionReads) rows(ctx context.Context, ids []string, group *string) ([]domain.SubscriptionReadsRow, error) {
	rendered, err := s.store.Render(ctx, ids, group)
	if err != nil {
		return nil, err
	}
	rows := make([]domain.SubscriptionReadsRow, 0, len(ids))
	for _, id := range ids {
		row, ok := rendered[id]
		if !ok {
			return nil, domain.ErrSubscriptionReadsServer
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// Serialized is SubscriptionSerializer(many=True).data of ids in order
// (renewal_available, a read-only field without attribute, is skipped).
func (s *SubscriptionReads) Serialized(ctx context.Context, ids []string, group *string) ([]any, error) {
	rows, err := s.rows(ctx, ids, group)
	if err != nil {
		return nil, err
	}
	now := s.now()
	items := make([]any, 0, len(rows))
	for _, row := range rows {
		payments, err := s.store.Payments(ctx, row.AssociateID, group)
		if err != nil {
			return nil, err
		}
		item, err := dto.SubscriptionReadsArchivedItem(row, payments, now)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

// subscriptionReadsAssociateColumns are Associate's concrete fields (and
// forward relation names and attnames) as values_list() renders them.
var subscriptionReadsAssociateColumns = func() map[string]string {
	columns := map[string]string{"pk": "associate_id"}
	for _, name := range []string{"associate_id", "picture_path", "first_name", "last_name", "sex", "tax_code", "born_date", "born_city",
		"address_city", "address", "address_cap", "nationality", "nationality_residence", "email", "phone", "phone_2", "phone_3", "phone_4",
		"phone_label", "phone_2_label", "phone_3_label", "phone_4_label", "is_minor", "disabled", "draft", "deleted", "family_role", "id_number",
		"creation_date", "migrated", "notes"} {
		columns[name] = name
	}
	for _, name := range []string{"user", "sport_association", "group", "family"} {
		columns[name], columns[name+"_id"] = name+"_id", name+"_id"
	}
	return columns
}()

// List follows subscription_list.
func (s *SubscriptionReads) List(ctx context.Context, request SubscriptionReadsRequest) (any, error) {
	query, identity := request.Query, request.Identity
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	if field := subscriptionReadsTruthy(query, "field"); field != "" {
		if subscriptionReadsScopedAthlete(identity) {
			return nil, domain.ErrSubscriptionReadsNotPorted
		}
		column, ok := subscriptionReadsAssociateColumns[field]
		if !ok {
			// A FieldError, or a relation path / transform values_list() would follow (not ported): both the 500 page.
			return nil, domain.ErrSubscriptionReadsServer
		}
		archived := false
		user := identity.ScopeUser.ID
		filter := domain.SubscriptionReadsFilter{OwnerUser: &user, Archived: &archived, Group: group, Scope: scope}
		timestamp := column == "creation_date"
		values, err := s.store.FieldValues(ctx, filter, column, timestamp)
		if err != nil {
			return nil, err
		}
		data := make([]any, 0, len(values))
		for _, value := range values {
			if value == nil {
				data = append(data, nil)
				continue
			}
			parsed, err := dto.ParseReferenceJSON([]byte(*value))
			if err != nil {
				return nil, err
			}
			if text, ok := parsed.(string); ok && timestamp {
				moment, err := time.Parse("2006-01-02T15:04:05.999999", text)
				if err != nil {
					return nil, err
				}
				parsed = dto.DRFUTCDateTime(moment)
			}
			data = append(data, parsed)
		}
		return domain.RequestObject{{Name: "data", Value: data}}, nil
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		if scope != nil {
			// scoped_queryset's family visibility for association-scoped athlete sessions is not ported.
			return nil, domain.ErrSubscriptionReadsNotPorted
		}
		return s.athleteList(ctx, identity.ScopeUser.ID, group)
	}
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	archived := false
	filter := domain.SubscriptionReadsFilter{AssociationID: &association, Archived: &archived, Group: group,
		Order: []domain.SubscriptionReadsOrder{{Column: "creation_date", Descending: true}}}
	if err := s.basicFilters(&filter, query); err != nil {
		return nil, err
	}
	if _, alternate := query.Lookup("m"); alternate {
		if raw, present := paymentReadsParam(query, "filter"); !present {
			status := int64(4)
			filter.Status = &status
		} else if raw != "all" {
			if err := subscriptionReadsStatus(&filter, raw); err != nil {
				return nil, err
			}
		}
		ids, err := s.store.Subscriptions(ctx, filter, -1, 0)
		if err != nil {
			return nil, err
		}
		rows, err := s.rows(ctx, ids, group)
		if err != nil {
			return nil, err
		}
		items := make([]any, len(rows))
		for i, row := range rows {
			items[i] = dto.SubscriptionReadsBasicItem(row, s.now())
		}
		return domain.RequestObject{{Name: "data", Value: subscriptionReadsIndexed(items)}, {Name: "meta", Value: domain.RequestObject{}}}, nil
	}
	if err := s.dateFilters(&filter, query); err != nil {
		return nil, err
	}
	search := subscriptionReadsTruthy(query, "query[generalSearch]")
	if prefix := subscriptionReadsTruthy(query, "query[prefix]"); prefix != "" {
		if _, present := query.Lookup("query[generalSearch]"); !present {
			search = "None"
		}
		search = prefix + search
	}
	if err := s.searchFilter(&filter, search); err != nil {
		return nil, err
	}
	s.additionalFilters(&filter, query)
	lazy, err := subscriptionReadsSorting(&filter, query)
	if err != nil {
		return nil, err
	}
	if request.Method == "POST" {
		title := "Iscrizioni filtrate"
		switch subscriptionReadsTruthy(query, "type") {
		case "athletes":
			title = "Tesserati"
		case "associates":
			title = "Libro Soci"
		}
		return nil, &SubscriptionReadsExport{filter: filter, group: group, association: association, title: title, lazy: lazy}
	}
	return s.page(ctx, filter, query, lazy, func(ids []string) ([]any, error) {
		rows, err := s.rows(ctx, ids, group)
		if err != nil {
			return nil, err
		}
		items := make([]any, 0, len(rows))
		for _, row := range rows {
			payments := domain.SubscriptionReadsPayments{Total: "0", ToPay: "0"}
			if row.AssociateID != nil {
				if payments, err = s.store.Payments(ctx, row.AssociateID, group); err != nil {
					return nil, err
				}
			}
			item, err := dto.SubscriptionReadsFastItem(row, payments, s.now())
			if err != nil {
				return nil, err
			}
			items = append(items, item)
		}
		return items, nil
	})
}

// basicFilters is _apply_basic_filters.
func (s *SubscriptionReads) basicFilters(filter *domain.SubscriptionReadsFilter, query domain.RequestObject) error {
	if tags := subscriptionReadsTruthy(query, "query[tags]"); tags != "" {
		ids := []string{}
		for _, part := range strings.Split(tags, ",") {
			id, err := subscriptionReadsUUID(part)
			if err != nil {
				return err
			}
			ids = append(ids, id)
		}
		if subscriptionReadsTruthy(query, "query[tags_and]") == "1" {
			filter.TagsAll = ids
		} else {
			filter.TagsAny = ids
		}
	}
	if raw := subscriptionReadsTruthy(query, "query[from_age]"); raw != "" {
		day, err := s.subscriptionReadsYearsAgo(raw)
		if err != nil {
			return err
		}
		filter.BornAtMost = &day
	}
	if raw := subscriptionReadsTruthy(query, "query[to_age]"); raw != "" {
		day, err := s.subscriptionReadsYearsAgo(raw)
		if err != nil {
			return err
		}
		filter.BornAfter = &day
	}
	if subscriptionReadsTruthy(query, "query[hide_associate_and_members]") == "1" {
		filter.ExcludeTypes = append(filter.ExcludeTypes, 2)
	}
	if subscriptionReadsTruthy(query, "query[hide_members]") == "1" {
		filter.ExcludeTypes = append(filter.ExcludeTypes, 3)
	}
	return nil
}

// subscriptionReadsStatus is status_flag=value: int(), out of the
// PositiveSmallIntegerField range an EmptyResultSet.
func subscriptionReadsStatus(filter *domain.SubscriptionReadsFilter, raw string) error {
	value, err := subscriptionReadsInt(raw)
	if err != nil {
		return err
	}
	if !value.IsInt64() || value.Int64() < 0 || value.Int64() > 32767 {
		filter.Empty = true
		return nil
	}
	status := value.Int64()
	filter.Status = &status
	return nil
}

// dateFilters is _apply_date_filters.
func (s *SubscriptionReads) dateFilters(filter *domain.SubscriptionReadsFilter, query domain.RequestObject) error {
	if raw := subscriptionReadsTruthy(query, "query[status_flag]"); raw != "" {
		if strings.Contains(raw, ",") {
			filter.StatusIn = []int64{}
			for _, part := range strings.Split(raw, ",") {
				value, err := subscriptionReadsInt(part)
				if err != nil {
					return err
				}
				// Values beyond 64 bits cannot match a smallint.
				if value.IsInt64() {
					filter.StatusIn = append(filter.StatusIn, value.Int64())
				}
			}
		} else if err := subscriptionReadsStatus(filter, raw); err != nil {
			return err
		}
	}
	today := s.today()
	switch subscriptionReadsTruthy(query, "query[current_year]") {
	case "2":
		filter.StartAfter = &today
	case "1":
		filter.Current = &today
	case "0":
		filter.EndBefore = &today
	case "3":
		start, end := subscriptionReadsTruthy(query, "query[period_start]"), subscriptionReadsTruthy(query, "query[period_end]")
		if start == "" || end == "" {
			return nil
		}
		from, okFrom := dto.PythonStrptimeDMYTime(start)
		to, okTo := dto.PythonStrptimeDMYTime(end)
		if !okFrom || !okTo {
			return nil // ValueError: logged and ignored
		}
		if from.After(to) {
			filter.Empty = true
			return nil
		}
		filter.OverlapStart, filter.OverlapEnd = &from, &to
	}
	return nil
}

// searchFilter is _apply_search_filter.
func (s *SubscriptionReads) searchFilter(filter *domain.SubscriptionReadsFilter, search string) error {
	if search == "" {
		return nil
	}
	if strings.Contains(strings.ToLower(search), "scade=") {
		if date, ok := dto.PythonStrptimeDMY(dto.PythonStripText(strings.ReplaceAll(search, "scade=", ""))); ok {
			filter.EndContains = &date
			return nil
		}
	}
	if strings.HasPrefix(search, "@=") {
		user := strings.ReplaceAll(search, "@=", "")
		filter.UserContains = &user
		return nil
	}
	return s.subscriptionReadsSmart(filter, subscriptionReadsDMY(search))
}

// additionalFilters is _apply_additional_filters.
func (s *SubscriptionReads) additionalFilters(filter *domain.SubscriptionReadsFilter, query domain.RequestObject) {
	if raw := subscriptionReadsTruthy(query, "query[type_of_associate]"); raw != "" {
		filter.CustomContains = &raw
	}
	if subscriptionReadsTruthy(query, "query[certificate_missing_flag]") == "1" {
		filter.MedicalMissing = true
	}
	if subscriptionReadsTruthy(query, "query[certificate_expired_flag]") == "1" {
		today := s.today()
		filter.MedicalExpiredBy = &today
	}
	if subscriptionReadsTruthy(query, "query[subscription_not_paid_flag]") == "1" {
		filter.PaymentUnpaid = true
	}
	switch subscriptionReadsTruthy(query, "type") {
	case "athletes":
		filter.Types = []int{2, 3}
	case "associates":
		filter.Types = []int{2, 1}
	}
}

// subscriptionReadsSorting is _apply_sorting; lazy reports an ordering refused after the page checks.
func subscriptionReadsSorting(filter *domain.SubscriptionReadsFilter, query domain.RequestObject) (bool, error) {
	ascending, descending := subscriptionReadsTruthy(query, "query[sort_lastname_asc_flag]"), subscriptionReadsTruthy(query, "query[sort_lastname_desc_flag]")
	if (ascending != "" && ascending != "0") || (descending != "" && descending != "0") {
		filter.Order = []domain.SubscriptionReadsOrder{{Column: "associate.last_name", Descending: descending == "1"}}
	}
	field := subscriptionReadsTruthy(query, "sort[field]")
	if field == "" {
		return false, nil
	}
	desc := subscriptionReadsTruthy(query, "sort[sort]") == "desc"
	switch field {
	case "associate":
		filter.Order = []domain.SubscriptionReadsOrder{{Column: "associate.first_name", Descending: desc}, {Column: "associate.last_name", Descending: desc}}
	case "age":
		filter.Order = []domain.SubscriptionReadsOrder{{Column: "age", Descending: desc}}
	default:
		order, err := subscriptionReadsOrder(field, desc, map[string]bool{"_has_future_renewal": true})
		if errors.Is(err, errSubscriptionReadsLazyOrder) {
			return true, nil
		}
		if err != nil {
			return false, err
		}
		filter.Order = order
	}
	return false, nil
}

// ListAll follows subscription_list_all.
func (s *SubscriptionReads) ListAll(ctx context.Context, request SubscriptionReadsRequest) (any, error) {
	query, identity := request.Query, request.Identity
	if subscriptionReadsScopedAthlete(identity) {
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	athlete := identity.ScopeUser.Role == domain.RoleAthlete
	key, keyPresent := paymentReadsParam(query, "sport_association")
	filter := domain.SubscriptionReadsFilter{Group: group, Scope: scope}
	athleteFilter := func() error {
		user := identity.ScopeUser.ID
		filter.UserID = &user
		if !keyPresent {
			filter.AssociationNull = true
			return nil
		}
		id, err := subscriptionReadsUUID(key)
		if err != nil {
			return err
		}
		filter.AssociationKey = &id
		return nil
	}
	ownerFilter := func() error {
		association, err := s.association(ctx, identity)
		if err != nil {
			return err
		}
		archived := false
		filter.AssociationID, filter.Archived = &association, &archived
		return nil
	}
	if raw, _ := paymentReadsParam(query, "preregistration"); raw == "1" {
		if athlete {
			if err := athleteFilter(); err != nil {
				return nil, err
			}
			filter.StatusIn = []int64{1, 2, 4, 5}
		} else if err := ownerFilter(); err != nil {
			return nil, err
		}
		filter.Order = []domain.SubscriptionReadsOrder{{Column: "associate.first_name"}, {Column: "associate.last_name"},
			{Column: "associate.tax_code"}, {Column: "creation_date", Descending: true}}
		ids, err := s.store.Subscriptions(ctx, filter, -1, 0)
		if err != nil {
			return nil, err
		}
		rows, err := s.rows(ctx, ids, group)
		if err != nil {
			return nil, err
		}
		key := func(row domain.SubscriptionReadsRow) string {
			associate := row.Associate
			return strings.ToLower(subscriptionReadsFullName(associate)) + "__" + subscriptionReadsPythonText(associate.TaxCode)
		}
		renewed := map[string]bool{}
		for _, row := range rows {
			if dto.SubscriptionReadsNextYear(row, s.now()) {
				if row.Associate == nil {
					return nil, domain.ErrSubscriptionReadsServer
				}
				renewed[key(row)] = true
			}
		}
		data, added := domain.RequestObject{}, map[string]bool{}
		for index, row := range rows {
			if row.Associate == nil {
				return nil, domain.ErrSubscriptionReadsServer // None.full_name_lower
			}
			name := key(row)
			if renewed[name] || added[name] {
				continue
			}
			data = append(data, domain.RequestField{Name: big.NewInt(int64(index)).String(), Value: subscriptionReadsAllItem(row, s.now(), false)})
			added[name] = true
		}
		return domain.RequestObject{{Name: "data", Value: data}}, nil
	}
	if athlete {
		if !keyPresent {
			return nil, domain.ErrSubscriptionReadsNotAllowed
		}
		if err := athleteFilter(); err != nil {
			return nil, err
		}
		filter.Order = []domain.SubscriptionReadsOrder{{Column: "creation_date", Descending: true}}
	} else if err := ownerFilter(); err != nil {
		return nil, err
	}
	if raw, present := paymentReadsParam(query, "current_year"); present && (raw == "1" || raw == "true") {
		today := s.today()
		filter.Current = &today
	}
	if raw, present := paymentReadsParam(query, "type"); present {
		switch raw {
		case "athletes":
			filter.Types = []int{2, 3}
		case "associates":
			filter.Types = []int{2, 1}
		}
	}
	if raw, present := paymentReadsParam(query, "course_id"); present {
		id, err := subscriptionReadsUUID(raw)
		if err != nil {
			return nil, err
		}
		filter.ExcludeCourse = &id
	}
	ids, err := s.store.Subscriptions(ctx, filter, -1, 0)
	if err != nil {
		return nil, err
	}
	rows, err := s.rows(ctx, ids, group)
	if err != nil {
		return nil, err
	}
	items := make([]any, 0, len(rows))
	for _, row := range rows {
		if row.Associate == nil {
			return nil, domain.ErrSubscriptionReadsServer // None.first_name
		}
		items = append(items, subscriptionReadsAllItem(row, s.now(), true))
	}
	return domain.RequestObject{{Name: "data", Value: subscriptionReadsIndexed(items)}, {Name: "meta", Value: domain.RequestObject{}}}, nil
}

func subscriptionReadsPythonText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// subscriptionReadsFullName is Associate.get_full_name(): '%s %s' % (first, last), stripped.
func subscriptionReadsFullName(associate *domain.PersonaAssociate) string {
	return dto.PythonStripText(subscriptionReadsPythonText(associate.FirstName) + " " + subscriptionReadsPythonText(associate.LastName))
}

func subscriptionReadsAllItem(row domain.SubscriptionReadsRow, now time.Time, season bool) domain.RequestObject {
	associate := row.Associate
	item := domain.RequestObject{{Name: "associate", Value: domain.RequestObject{{Name: "first_name", Value: dto.PersonaOptional(associate.FirstName)},
		{Name: "last_name", Value: dto.PersonaOptional(associate.LastName)}, {Name: "associate_id", Value: associate.ID}}},
		{Name: "subscription_id", Value: row.ID}, {Name: "current_year", Value: dto.SubscriptionReadsCurrent(row, now)}}
	if season {
		item = append(item, domain.RequestField{Name: "season", Value: dto.SubscriptionReadsSeason(row)})
	}
	return item
}

// Archived follows subscription_list_archived.
func (s *SubscriptionReads) Archived(ctx context.Context, request SubscriptionReadsRequest) (any, error) {
	query, identity := request.Query, request.Identity
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrSubscriptionReadsAthlete
	}
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	archived := true
	filter := domain.SubscriptionReadsFilter{AssociationID: &association, Archived: &archived, Group: group, Scope: scope,
		Order: []domain.SubscriptionReadsOrder{{Column: "creation_date", Descending: true}}}
	if search := subscriptionReadsTruthy(query, "query[generalSearch]"); search != "" {
		if err := s.subscriptionReadsSmart(&filter, subscriptionReadsDMY(search)); err != nil {
			return nil, err
		}
	}
	lazy := false
	if field := subscriptionReadsTruthy(query, "sort[field]"); field != "" {
		desc := subscriptionReadsTruthy(query, "sort[sort]") == "desc"
		if field == "associate" {
			filter.Order = []domain.SubscriptionReadsOrder{{Column: "associate.first_name", Descending: desc}, {Column: "associate.last_name", Descending: desc}}
		} else {
			order, err := subscriptionReadsOrder(field, desc, map[string]bool{"_has_future_renewal": true})
			switch {
			case errors.Is(err, errSubscriptionReadsLazyOrder):
				lazy = true
			case err != nil:
				return nil, err
			default:
				filter.Order = order
			}
		}
	}
	ascending, descending := subscriptionReadsTruthy(query, "query[sort_lastname_asc_flag]"), subscriptionReadsTruthy(query, "query[sort_lastname_desc_flag]")
	if ascending != "" || descending != "" {
		filter.Order = []domain.SubscriptionReadsOrder{{Column: "associate.last_name", Descending: descending == "1"}}
		lazy = false
	}
	day, month, err := s.store.BalanceStart(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	return s.page(ctx, filter, query, lazy, func(ids []string) ([]any, error) {
		rows, err := s.rows(ctx, ids, group)
		if err != nil {
			return nil, err
		}
		now := s.now()
		items := make([]any, 0, len(rows))
		for _, row := range rows {
			payments, err := s.store.Payments(ctx, row.AssociateID, group)
			if err != nil {
				return nil, err
			}
			item, err := dto.SubscriptionReadsArchivedItem(row, payments, now)
			if err != nil {
				return nil, err
			}
			year, err := BalanceSheetRange(row.CreationDate, day, month)
			if err != nil {
				return nil, domain.ErrSubscriptionReadsServer
			}
			current := !year.To.Before(now.UTC())
			item["current_year"], item["next_years"] = current, row.CreationDate.After(year.To)
			renewal := false
			if !current {
				future, err := s.futureRenewal(ctx, row, group)
				if err != nil {
					return nil, err
				}
				renewal = !future
			}
			item["renewal_available"] = renewal
			if row.MedicalID != nil && row.MedicalDocumentID != nil {
				item["medical_document"] = *row.MedicalDocumentID
			}
			items = append(items, item)
		}
		return items, nil
	})
}

// futureRenewal is Subscription.has_future_renewal().
func (s *SubscriptionReads) futureRenewal(ctx context.Context, row domain.SubscriptionReadsRow, group *string) (bool, error) {
	if row.AssociateID == nil || row.Associate == nil {
		return false, nil
	}
	if row.Associate.TaxCode != nil {
		return row.FutureRenewal, nil
	}
	if row.EndDate == nil {
		return false, domain.ErrSubscriptionReadsServer // start_date__gte=None
	}
	return s.store.NullTaxRenewal(ctx, row.AssociationID, *row.EndDate, group)
}

// Info follows subscription_info.
func (s *SubscriptionReads) Info(ctx context.Context, request SubscriptionReadsRequest, uid string) (any, error) {
	identity := request.Identity
	parsed, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	if subscriptionReadsScopedAthlete(identity) {
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	id := parsed.String()
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	var association string
	if identity.ScopeUser.Role == domain.RoleAthlete {
		allowed, err := s.store.AllowedForAthlete(ctx, identity.ScopeUser.ID, group, scope)
		if err != nil {
			return nil, err
		}
		found := false
		for _, candidate := range allowed {
			// uid not in [str(id) ...]: the raw path text against the canonical form.
			found = found || candidate == uid
		}
		if !found {
			return nil, &domain.PermissionDenied{Detail: "Subscription not found."}
		}
		row, err := s.store.Subscription(ctx, id, group, scope, nil, nil)
		if err != nil {
			return nil, err
		}
		if row == nil {
			return nil, &domain.PermissionDenied{Detail: "Sport association id not found."}
		}
		live, err := s.store.LiveAssociation(ctx, row.AssociationID)
		if err != nil {
			return nil, err
		}
		if !live {
			return nil, &domain.PermissionDenied{Detail: "Sport association not found."}
		}
		association = row.AssociationID
	} else if association, err = s.association(ctx, identity); err != nil {
		return nil, err
	}
	row, err := s.store.Subscription(ctx, id, group, scope, nil, nil)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrSubscriptionReadsNotFound
	}
	if row.AssociationID != association {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
	}
	courses, err := s.infoCourses(ctx, row.ID, group, scope)
	if err != nil {
		return nil, err
	}
	if row.Associate == nil {
		return nil, domain.ErrSubscriptionReadsServer // get_plain_medical_label() dereferences the associate
	}
	if row.DocumentID == nil && s.tasks == nil {
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	tags, err := dto.ParseReferenceJSON([]byte(row.Tags))
	if err != nil {
		return nil, err
	}
	tagIDs := []any{}
	for _, tag := range tags.([]any) {
		value, _ := tag.(domain.RequestObject).Lookup("tag_id")
		tagIDs = append(tagIDs, value)
	}
	info := dto.SubscriptionReadsInfo(*row, tagIDs, s.now())
	card, err := s.store.Card(ctx, row.AssociationID)
	if err != nil {
		return nil, err
	}
	configuration := map[string]any{"emit_only_on_approval": false, "customized_template": map[string]any{}}
	if card != nil {
		var template any
		if card.CustomizedTemplate != nil {
			template = json.RawMessage(*card.CustomizedTemplate)
		}
		configuration = map[string]any{"emit_only_on_approval": card.EmitOnlyOnApproval, "customized_template": template}
	}
	info["membership_card_configuration"] = configuration
	files, err := s.store.Files(ctx, row.ID, scope)
	if err != nil {
		return nil, err
	}
	items := make([]any, len(files))
	for i, file := range files {
		items[i] = map[string]any{"subscription_file_id": file.ID, "document_id": file.DocumentID, "filename": file.Filename,
			"document_token": file.Token, "creation_date": dto.DRFUTCDateTime(file.CreationDate)}
	}
	info["subscription_files"] = items
	persona, err := s.persona(ctx, *row.AssociateID, group)
	if err != nil {
		return nil, err
	}
	info["associate"] = persona
	if row.DocumentID == nil {
		// print_document_subscription.delay(str(subscription_id), request.headers.get('authorization')).
		var header any
		if request.Authorization != nil {
			header = *request.Authorization
		}
		if _, err := s.tasks.Publish(ctx, celery.Task{Name: "print_document_subscription", Args: []any{row.ID, header}}); err != nil {
			return nil, err
		}
	}
	return map[string]any{"data": map[string]any{"info": info, "courses": courses, "payments": []any{}}}, nil
}

// persona is personas_serializers.AssociateSerializer(subscription.associate).
func (s *SubscriptionReads) persona(ctx context.Context, associate string, group *string) (any, error) {
	row, err := s.store.Persona(ctx, associate)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrSubscriptionReadsServer
	}
	relations, err := s.store.Relations(ctx, []string{row.ID})
	if err != nil {
		return nil, err
	}
	tutors := make([]string, len(relations))
	for i, relation := range relations {
		tutors[i] = relation.ID
	}
	second, err := s.store.Tutors(ctx, tutors, group)
	if err != nil {
		return nil, err
	}
	nested := make([]string, len(second))
	for i, relation := range second {
		nested[i] = relation.ID
	}
	third, err := s.store.Tutors(ctx, nested, group)
	if err != nil {
		return nil, err
	}
	family := []string{}
	if row.FamilyID != nil {
		if family, err = s.store.FamilyMembers(ctx, *row.FamilyID, row.ID, group); err != nil {
			return nil, err
		}
	}
	return dto.PersonaInfo(*row, family, relations, second, third, s.today()), nil
}

// infoCourses are the courses list: CourseDetailSerializer per live course
// subscription plus its CourseSubscriptionOverviewSerializer and, for
// multi-payment ones, the installments; sorted by the course creation date
// text, descending (stable).
func (s *SubscriptionReads) infoCourses(ctx context.Context, subscription string, group, scope *string) ([]any, error) {
	rows, err := s.store.Courses(ctx, subscription, scope)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return []any{}, nil
	}
	// course_subs_dict: the last course subscription of a course wins.
	byCourse := map[string]int{}
	keys := make([]string, len(rows))
	for i, row := range rows {
		byCourse[row.CourseID] = i
		keys[i] = row.ID
	}
	tx, err := s.courses.BeginCourseSubscription(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	persons, err := tx.People(ctx, []string{subscription})
	if err != nil {
		return nil, err
	}
	installments, err := tx.Installments(ctx, keys, group)
	if err != nil {
		return nil, err
	}
	payments, err := tx.MembershipPayments(ctx, keys, group)
	if err != nil {
		return nil, err
	}
	carnets, err := tx.Carnets(ctx, keys)
	if err != nil {
		return nil, err
	}
	listed, err := s.store.Installments(ctx, keys, group, scope)
	if err != nil {
		return nil, err
	}
	now := s.now()
	type course struct {
		created string
		item    map[string]any
	}
	courses := make([]course, 0, len(rows))
	for _, row := range rows {
		chosen := rows[byCourse[row.CourseID]]
		render := dto.CourseSubscriptionRender{Row: chosen.CourseSubscriptionRow, Payments: payments[chosen.ID]}
		if person, ok := persons[subscription]; ok {
			render.Person = &person
		}
		for _, installment := range installments {
			if installment.CourseSubscription == chosen.ID {
				render.Installments = append(render.Installments, installment)
			}
		}
		for _, carnet := range carnets {
			if carnet.CourseSubscription == chosen.ID {
				render.Carnets = append(render.Carnets, carnet)
			}
		}
		overview, err := dto.CourseSubscriptionItem(render, now)
		if err != nil {
			return nil, domain.ErrSubscriptionReadsServer
		}
		item := subscriptionReadsCourseDetail(row.Course)
		item["course_subscription"] = overview
		if chosen.MultiPayments {
			entries := []any{}
			for _, installment := range listed {
				if installment.CourseSubscription != chosen.ID {
					continue
				}
				paid := installment.Paid
				if installment.PaymentID != nil && installment.PaymentPaid != nil {
					paid = *installment.PaymentPaid
				}
				entries = append(entries, map[string]any{"course_subscription_installment_id": installment.ID,
					"payment_id": dto.PersonaOptional(installment.PaymentID), "amount": dto.SubscriptionReadsFloat(installment.Amount),
					"payment_date": dto.DRFUTCDateTime(installment.PaymentDate), "paid": paid})
			}
			item["installments"] = entries
		}
		courses = append(courses, course{created: dto.DRFUTCDateTime(row.Course.CreationDate), item: item})
	}
	sort.SliceStable(courses, func(i, j int) bool { return courses[i].created > courses[j].created })
	result := make([]any, len(courses))
	for i, course := range courses {
		result[i] = course.item
	}
	return result, nil
}

// subscriptionReadsCourseDetail is CourseDetailSerializer ('__all__': the M2M as primary keys).
func subscriptionReadsCourseDetail(row domain.CourseOverviewRow) map[string]any {
	item := courseSerializerData(row)
	delete(item, "athletes")
	return item
}

// Calendar follows subscription_calendar.
func (s *SubscriptionReads) Calendar(ctx context.Context, request SubscriptionReadsRequest, uid string) (any, error) {
	identity := request.Identity
	parsed, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	if subscriptionReadsScopedAthlete(identity) {
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	var row *domain.SubscriptionReadsRow
	if identity.ScopeUser.Role == domain.RoleAthlete {
		user := identity.ScopeUser.ID
		row, err = s.store.Subscription(ctx, parsed.String(), group, scope, &user, nil)
	} else {
		association, err := s.association(ctx, identity)
		if err != nil {
			return nil, err
		}
		row, err = s.store.Subscription(ctx, parsed.String(), group, scope, nil, &association)
		if err != nil {
			return nil, err
		}
	}
	if err != nil {
		return nil, err
	}
	events := []any{}
	if row != nil {
		registries, err := s.store.Registries(ctx, row.ID, scope)
		if err != nil {
			return nil, err
		}
		for _, registry := range registries {
			if registry.Events == nil {
				continue
			}
			value, err := dto.ParseReferenceJSON([]byte(*registry.Events))
			if err != nil {
				return nil, err
			}
			if !dto.PythonTruthy(value) {
				continue
			}
			list, ok := value.([]any)
			if !ok {
				return nil, domain.ErrSubscriptionReadsServer // iterating a dict or string yields keys/characters: TypeError
			}
			title := "None"
			if registry.CourseTitle != nil {
				title = *registry.CourseTitle
			}
			for _, entry := range list {
				event, ok := entry.(domain.RequestObject)
				if !ok {
					return nil, domain.ErrSubscriptionReadsServer
				}
				values := map[string]any{}
				for _, name := range []string{"title", "start", "end", "allDay"} {
					value, present := event.Lookup(name)
					if !present {
						return nil, domain.ErrSubscriptionReadsServer // KeyError
					}
					values[name] = value
				}
				events = append(events, domain.RequestObject{{Name: "title", Value: dto.PythonStr(values["title"]) + " (" + title + ")"},
					{Name: "start", Value: values["start"]}, {Name: "end", Value: values["end"]}, {Name: "allDay", Value: values["allDay"]}})
			}
		}
	}
	return domain.RequestObject{{Name: "data", Value: domain.RequestObject{{Name: "events", Value: events}}}}, nil
}
