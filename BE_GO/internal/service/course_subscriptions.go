package service

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type CourseSubscriptions struct {
	store domain.CourseSubscriptionStore
	now   func() time.Time
}

func NewCourseSubscriptions(store domain.CourseSubscriptionStore, now func() time.Time) *CourseSubscriptions {
	return &CourseSubscriptions{store: store, now: now}
}

var (
	// ErrCourseSubscriptionExportUnsupported refuses list's POST: the PDF and
	// Excel export (current_view_course_subscription) is not ported. Its
	// invalid formats are Django's 500 as well.
	ErrCourseSubscriptionExportUnsupported = errors.New("course subscription export is not ported")
	// ErrCourseSubscriptionNotPorted refuses inputs this port does not
	// reproduce, before any write: events given as an object or a string,
	// unmatched installments sharing a payment, HTML-form JSON fields, a
	// changed primary key, and a soft delete whose row shares its payment with
	// an installment.
	ErrCourseSubscriptionNotPorted = errors.New("course subscription input is not ported")
)

// CourseSubscriptionValidationError is a 400 field map: lists of codes from
// the serializer, plain texts from the view's ValidationError.
type CourseSubscriptionValidationError struct{ Fields map[string]any }

func (e *CourseSubscriptionValidationError) Error() string {
	return "invalid course subscription input"
}

// CourseSubscriptionRequest carries what the view reads besides the body.
type CourseSubscriptionRequest struct {
	Query       domain.RequestObject
	GroupHeader string
}

var courseSubscriptionNotAssociation = &domain.PermissionDenied{Detail: "User is not a sport association"}

// association is User.sport_association: athletes are refused, an owner
// without an association is a 500.
func (s *CourseSubscriptions) association(ctx context.Context, tx domain.CourseSubscriptionTransaction, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", courseSubscriptionNotAssociation
	}
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return "", domain.ErrCourseSubscriptionServer
	}
	return association, err
}

// queryUUID is a UUIDField lookup value: one the field rejects raises while
// the queryset is built (500).
func queryUUID(value string) (*string, error) {
	id, err := paymentInfoParseUUID(value)
	if err != nil {
		return nil, domain.ErrCourseSubscriptionServer
	}
	text := id.String()
	return &text, nil
}

func (s *CourseSubscriptions) begin(ctx context.Context) (domain.CourseSubscriptionTransaction, error) {
	return s.store.BeginCourseSubscription(ctx)
}

// render is the serializer for rows (many=True).
func (s *CourseSubscriptions) render(ctx context.Context, tx domain.CourseSubscriptionTransaction, rows []domain.CourseSubscriptionRow,
	group *string) ([]dto.CourseSubscriptionRender, error) {
	keys, people := make([]string, 0, len(rows)), []string{}
	for _, row := range rows {
		keys = append(keys, row.ID)
		if row.SubscriptionID != nil {
			people = append(people, *row.SubscriptionID)
		}
	}
	persons, err := tx.People(ctx, people)
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
	renders := make([]dto.CourseSubscriptionRender, 0, len(rows))
	for _, row := range rows {
		render := dto.CourseSubscriptionRender{Row: row, Payments: payments[row.ID]}
		if row.SubscriptionID != nil {
			if person, ok := persons[*row.SubscriptionID]; ok {
				render.Person = &person
			}
		}
		for _, installment := range installments {
			if installment.CourseSubscription == row.ID {
				render.Installments = append(render.Installments, installment)
			}
		}
		for _, carnet := range carnets {
			if carnet.CourseSubscription == row.ID {
				render.Carnets = append(render.Carnets, carnet)
			}
		}
		renders = append(renders, render)
	}
	return renders, nil
}

func (s *CourseSubscriptions) items(renders []dto.CourseSubscriptionRender) ([]map[string]any, error) {
	now := s.now()
	items := make([]map[string]any, 0, len(renders))
	for _, render := range renders {
		item, err := dto.CourseSubscriptionItem(render, now)
		if err != nil {
			return nil, domain.ErrCourseSubscriptionServer
		}
		items = append(items, item)
	}
	return items, nil
}

// List follows CourseSubscriptionViewSet.list ({'data': [...]}, unordered);
// export (POST) is refused after the queryset is built.
func (s *CourseSubscriptions) List(ctx context.Context, identity domain.ResolvedIdentity, request CourseSubscriptionRequest, export bool) (map[string]any, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	query := domain.CourseSubscriptionQuery{AssociationID: association}
	if value, present := request.Query.Lookup("include_deleted"); present {
		text, _ := value.(string)
		query.IncludeDeleted = strings.ToLower(text) == "true"
	}
	for _, filter := range []struct {
		name   string
		target **string
	}{{"course_id", &query.CourseID}, {"subscription_id", &query.SubscriptionID}} {
		value, present := request.Query.Lookup(filter.name)
		if !present {
			continue
		}
		text, _ := value.(string)
		if *filter.target, err = queryUUID(text); err != nil {
			return nil, err
		}
	}
	if export {
		return nil, ErrCourseSubscriptionExportUnsupported
	}
	rows, err := tx.CourseSubscriptions(ctx, query)
	if err != nil {
		return nil, err
	}
	renders, err := s.render(ctx, tx, rows, supplierGroup(request.GroupHeader))
	if err != nil {
		return nil, err
	}
	items, err := s.items(renders)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": items}, nil
}

// DeleteWithoutPK is the viewset's delete() on the list route: get_object()
// reads request.user.sport_association, then fails without a pk.
func (s *CourseSubscriptions) DeleteWithoutPK(ctx context.Context, identity domain.ResolvedIdentity) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = s.association(ctx, tx, identity); err != nil {
		return err
	}
	return domain.ErrCourseSubscriptionServer
}

// object is get_object(): django.shortcuts.get_object_or_404 lets a pk the
// UUIDField rejects raise (500).
func (s *CourseSubscriptions) object(ctx context.Context, tx domain.CourseSubscriptionTransaction, identity domain.ResolvedIdentity, raw string,
	includeDeleted bool) (domain.CourseSubscriptionRow, string, error) {
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return domain.CourseSubscriptionRow{}, "", err
	}
	id, err := queryUUID(raw)
	if err != nil {
		return domain.CourseSubscriptionRow{}, "", err
	}
	rows, err := tx.CourseSubscriptions(ctx, domain.CourseSubscriptionQuery{AssociationID: association, ID: id, IncludeDeleted: includeDeleted, Lock: true})
	if err != nil {
		return domain.CourseSubscriptionRow{}, "", err
	}
	if len(rows) == 0 {
		return domain.CourseSubscriptionRow{}, "", domain.ErrCourseSubscriptionNotFound
	}
	return rows[0], association, nil
}

func (s *CourseSubscriptions) auditContext(ctx context.Context, tx domain.CourseSubscriptionTransaction, identity domain.ResolvedIdentity,
	audit domain.LoginAuditContext, group *string) (domain.CourseSubscriptionAudit, error) {
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return domain.CourseSubscriptionAudit{}, err
	}
	return domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, audit), ActorAssociation: actor,
		At: s.now().UTC().Truncate(time.Microsecond), Group: group}, nil
}

// Delete is the viewset's delete(): the model's soft delete. On the delete
// route get_object() includes deleted rows.
func (s *CourseSubscriptions) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, includeDeleted bool,
	request CourseSubscriptionRequest, audit domain.LoginAuditContext) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, _, err := s.object(ctx, tx, identity, raw, includeDeleted)
	if err != nil {
		return err
	}
	group := supplierGroup(request.GroupHeader)
	shared, err := tx.SharedPayment(ctx, row, group)
	if err != nil {
		return err
	}
	if shared {
		// The save would restore a payment the installments' signals deleted
		// (an autocommitted IntegrityError after the deletes).
		return ErrCourseSubscriptionNotPorted
	}
	context, err := s.auditContext(ctx, tx, identity, audit, group)
	if err != nil {
		return err
	}
	if err = tx.DeleteInstallmentsOf(ctx, row.ID, context); err != nil {
		return err
	}
	save := domain.CourseSubscriptionSave{Row: row, Unseen: row.Deleted}
	save.Row.Deleted = true
	save.Representation = dto.CourseSubscriptionRepr(save.Row, dto.CourseSubscriptionMemory{})
	if !row.Deleted {
		// auditlog writes the entry before the UPDATE: the row still resolves.
		save.Changes = map[string][2]string{"deleted": {"False", "True"}}
		save.AssociationID, save.ResolutionPath = row.CourseAssociationID, "course.sport_association"
	}
	if err = tx.SaveCourseSubscription(ctx, save, context); err != nil {
		return err
	}
	return tx.Commit()
}

// bulkIDs is the course_subscription_id__in value: None is not iterable, a
// string is iterated by character, a dict by key; None members are dropped
// and every other member must be a UUID (UUIDField.to_python).
func bulkIDs(value any) ([]string, error) {
	var members []any
	switch v := value.(type) {
	case []any:
		members = v
	case string:
		for _, r := range v {
			members = append(members, string(r))
		}
	case domain.RequestObject:
		for _, field := range v {
			members = append(members, field.Name)
		}
	default:
		return nil, domain.ErrCourseSubscriptionServer
	}
	ids := []string{}
	for _, member := range members {
		switch member.(type) {
		case nil:
			continue
		case []any, domain.RequestObject:
			return nil, domain.ErrCourseSubscriptionServer
		}
		id, err := djangoUUIDValue(member)
		if err != nil {
			return nil, domain.ErrCourseSubscriptionServer
		}
		ids = append(ids, id)
	}
	return ids, nil
}

// BulkDelete follows bulk_delete: request.data.get(...) (a body that is not a
// dict is a 500), then QuerySet.delete() of the matching live rows.
func (s *CourseSubscriptions) BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, request CourseSubscriptionRequest, body any,
	audit domain.LoginAuditContext) error {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return domain.ErrCourseSubscriptionServer
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var value any = []any{}
	if found, present := object.Lookup("course_subscription_ids"); present {
		value = found
	}
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return err
	}
	ids, err := bulkIDs(value)
	if err != nil {
		return err
	}
	query := domain.CourseSubscriptionQuery{AssociationID: association, IDs: ids, Lock: true}
	if identity.ImpersonationAssociationID != "" {
		scope := identity.ImpersonationAssociationID
		query.Scope = &scope
	}
	rows, err := tx.CourseSubscriptions(ctx, query)
	if err != nil {
		return err
	}
	group := supplierGroup(request.GroupHeader)
	context, err := s.auditContext(ctx, tx, identity, audit, group)
	if err != nil {
		return err
	}
	if err = tx.BulkDelete(ctx, rows, context); err != nil {
		return err
	}
	return tx.Commit()
}

func courseSubscriptionInvalid(fields map[string]any) error {
	return &CourseSubscriptionValidationError{Fields: fields}
}

func sameTime(left, right *time.Time) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return left.Equal(*right)
}

func sameText(left, right *string) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return *left == *right
}

// courseSubscriptionAuditText is auditlog's str() of a field value.
func courseSubscriptionAuditTime(value *time.Time) string {
	if value == nil {
		return "None"
	}
	moment := value.UTC()
	if moment.Nanosecond()/1000 == 0 {
		return moment.Format("2006-01-02 15:04:05")
	}
	return moment.Format("2006-01-02 15:04:05.000000")
}

func courseSubscriptionAuditText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func courseSubscriptionAuditJSON(text *string, value any, set bool) (string, error) {
	if set {
		return string(dto.PythonJSONDumpsSorted(value)), nil
	}
	if text == nil {
		return "null", nil
	}
	parsed, err := dto.ParseProfileTablesJSON([]byte(*text))
	if err != nil {
		return "", err
	}
	return string(dto.PythonJSONDumpsSorted(parsed)), nil
}

// courseSubscriptionChanges is auditlog's update diff of the saved instance
// against the stored row.
func courseSubscriptionChanges(old, row domain.CourseSubscriptionRow, write dto.CourseSubscriptionWrite) (map[string][2]string, error) {
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	flag := func(value bool) string { return map[bool]string{true: "True", false: "False"}[value] }
	number := strconv.Itoa
	diff("course_subscription_id", old.ID, row.ID)
	diff("course", old.CourseID, row.CourseID)
	diff("paid", flag(old.Paid), flag(row.Paid))
	diff("creation_date", courseSubscriptionAuditTime(&old.CreationDate), courseSubscriptionAuditTime(&row.CreationDate))
	diff("subscription", courseSubscriptionAuditText(old.SubscriptionID), courseSubscriptionAuditText(row.SubscriptionID))
	diff("multi_payments", flag(old.MultiPayments), flag(row.MultiPayments))
	diff("one_fee_payment", flag(old.OneFeePayment), flag(row.OneFeePayment))
	before, err := courseSubscriptionAuditJSON(old.MultipleQuote, nil, false)
	if err != nil {
		return nil, err
	}
	after, err := courseSubscriptionAuditJSON(row.MultipleQuote, write.Quote, write.QuoteSet)
	if err != nil {
		return nil, err
	}
	diff("multiple_quote", before, after)
	diff("billed_frequency", number(old.BilledFrequency), number(row.BilledFrequency))
	diff("billed_from", courseSubscriptionAuditTime(old.BilledFrom), courseSubscriptionAuditTime(row.BilledFrom))
	diff("billed_until", courseSubscriptionAuditTime(old.BilledUntil), courseSubscriptionAuditTime(row.BilledUntil))
	diff("billed_from_day_of_month", number(old.BilledFromDayOfMonth), number(row.BilledFromDayOfMonth))
	diff("membership_active", flag(old.MembershipActive), flag(row.MembershipActive))
	diff("membership_fee", courseSubscriptionAuditText(old.MembershipFee), courseSubscriptionAuditText(row.MembershipFee))
	diff("auto_renewal", flag(old.AutoRenewal), flag(row.AutoRenewal))
	return changes, nil
}

// Retrieve is the router detail route's stock retrieve through the viewset's
// get_object(): User.sport_association, the live rows of its courses
// (django.shortcuts.get_object_or_404: a pk the UUIDField rejects is a 500),
// then CourseSubscriptionOverviewSerializer.
func (s *CourseSubscriptions) Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string,
	request CourseSubscriptionRequest) (map[string]any, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	row, _, err := s.object(ctx, tx, identity, raw, false)
	if err != nil {
		return nil, err
	}
	renders, err := s.render(ctx, tx, []domain.CourseSubscriptionRow{row}, supplierGroup(request.GroupHeader))
	if err != nil {
		return nil, err
	}
	items, err := s.items(renders)
	if err != nil {
		return nil, err
	}
	return items[0], nil
}
