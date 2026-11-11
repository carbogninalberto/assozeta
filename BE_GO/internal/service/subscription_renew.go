package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// SubscriptionRenewFailure is subscription_renew's 400 {"status": "error", "msg": str(e)}.
type SubscriptionRenewFailure struct{ Text string }

func (e *SubscriptionRenewFailure) Error() string { return "subscription renew failed: " + e.Text }

// SubscriptionRenewResult is the view's {"status": "success", "payment_id": ...}.
type SubscriptionRenewResult struct{ PaymentID *string }

// SubscriptionRenewals ports subscription_renew (subscription-renew.md).
type SubscriptionRenewals struct {
	store     domain.SubscriptionRenewStore
	creates   *SubscriptionCreates
	enrolment *CourseEnrolment
}

// WithEnrolment sets the course-enrolment port the renewal's courses run through.
func (s *SubscriptionRenewals) WithEnrolment(enrolment *CourseEnrolment) *SubscriptionRenewals {
	s.enrolment = enrolment
	return s
}

func NewSubscriptionRenewals(store domain.SubscriptionRenewStore, creates *SubscriptionCreates) *SubscriptionRenewals {
	return &SubscriptionRenewals{store: store, creates: creates}
}

// renewError maps create_subscription and post-processing errors: APIExceptions
// propagate, Python exceptions with a known str() are the 400 body, other
// exceptions are not ported.
func renewError(err error) error {
	var failure *domain.SubscriptionCreateFailure
	if errors.As(err, &failure) {
		return &SubscriptionRenewFailure{Text: failure.Text}
	}
	if errors.Is(err, domain.ErrSubscriptionCreateServer) {
		return domain.ErrSubscriptionCreateNotPorted
	}
	return err
}

// Renew follows subscription_renew for owners and collaborators.
func (s *SubscriptionRenewals) Renew(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionCreateRequest) (*SubscriptionRenewResult, error) {
	user := identity.ScopeUser
	// Form bodies and X-Group-Id are not ported.
	if user == nil || request.Form || strings.TrimSpace(request.Group) != "" {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	request.Audit = paymentCategoryAuditContext(identity, request.Audit)
	committed := false
	tx, err := s.store.BeginSubscriptionRenew(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	// Only an association account chooses the account of the renewed subscription.
	byAssociation := user.Role == domain.RoleAssociation
	if !byAssociation {
		// request.user becomes the owner of data['sport_association']['sport_association_id'];
		// the audit actor and create_subscription's requester stay the member account.
		owner, err := s.renewRequesterOwner(ctx, tx, identity, request.Body)
		if err != nil {
			return nil, err
		}
		request.RequesterID = user.ID
		identity.ScopeUser = owner
		user = owner
	}
	// The log line reads request.user.sport_association first.
	count, err := tx.AssociationCount(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if count != 1 {
		return nil, renewError(subscriptionAssociationGet(count))
	}
	data, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, renewError(pythonAttributeError(request.Body, "keys"))
	}
	if err = subscriptionCreateRefuse(data, subscriptionCreateToday(s.creates.now())); err != nil {
		return nil, err
	}
	keys := []string{}
	request.StorageKeys = &keys
	defer func() {
		if !committed {
			s.creates.cleanupStorage(ctx, keys)
		}
	}()
	created, err := s.creates.createSubscription(ctx, tx, identity, data, request)
	if err != nil {
		return nil, renewError(err)
	}
	if err = s.cleanPayments(ctx, tx, identity, created, request); err != nil {
		return nil, renewError(err)
	}
	if err = s.tags(ctx, tx, data, created); err != nil {
		return nil, renewError(err)
	}
	if err = s.courses(ctx, tx, identity, data, created, request); err != nil {
		return nil, renewError(err)
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	committed = true
	s.creates.afterCommit(ctx, identity, created, request)
	return &SubscriptionRenewResult{PaymentID: created.result.PaymentID}, nil
}

// renewRequesterOwner is scoped_queryset(request, SportAssociation.objects).get(
// sport_association_id=data['sport_association']['sport_association_id']).user,
// before the view's try block: every failure is the 500 page.
func (s *SubscriptionRenewals) renewRequesterOwner(ctx context.Context, tx domain.SubscriptionRenewTransaction, identity domain.ResolvedIdentity,
	body any) (*domain.Account, error) {
	data, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrSubscriptionCreateServer
	}
	value, _ := data.Lookup("sport_association")
	association, ok := value.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrSubscriptionCreateServer
	}
	raw, _ := association.Lookup("sport_association_id")
	text, ok := raw.(string)
	if !ok {
		// A missing key, None (IS NULL) or another type: KeyError, DoesNotExist or ValidationError
		// (an int is uuid.UUID(int=...), not ported).
		if _, isNumber := raw.(json.Number); isNumber {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		return nil, domain.ErrSubscriptionCreateServer
	}
	id, err := paymentInfoParseUUID(text)
	if err != nil {
		return nil, domain.ErrSubscriptionCreateServer
	}
	owner, err := tx.RenewAssociationOwner(ctx, id.String())
	if err != nil {
		return nil, err
	}
	if scope := identity.ImpersonationAssociationID; scope != "" && scope != id.String() {
		// An association-scoped session only finds its own association.
		owner = nil
	}
	if owner == nil {
		return nil, domain.ErrSubscriptionCreateServer
	}
	// A member account renews its own subscriptions and those of its family, in the
	// association they belong to.
	allowed, err := renewMemberAllowed(ctx, tx, identity.ScopeUser.ID, id.String(), data)
	if err != nil {
		return nil, err
	}
	if !allowed {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
	}
	if owner.Role != domain.RoleAssociation {
		// An association whose user is not an owner account is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	return owner, nil
}

// renewMemberAllowed is _member_renewal_association's check on
// data.get('associate_data'): a non-empty string associate_id through uuid.UUID,
// else (associate_id None) a non-empty string tax_code; anything else is refused.
func renewMemberAllowed(ctx context.Context, tx domain.SubscriptionRenewTransaction, requester, association string,
	data domain.RequestObject) (bool, error) {
	value, _ := data.Lookup("associate_data")
	associate, _ := value.(domain.RequestObject)
	identifier, _ := associate.Lookup("associate_id")
	switch v := identifier.(type) {
	case string:
		if v == "" {
			return false, nil
		}
		parsed, err := paymentInfoParseUUID(v)
		if err != nil {
			return false, nil
		}
		key := parsed.String()
		return tx.MemberRenewable(ctx, requester, association, &key, nil)
	case nil:
		taxCode, _ := associate.Lookup("tax_code")
		text, isText := taxCode.(string)
		if !isText || text == "" {
			return false, nil
		}
		return tx.MemberRenewable(ctx, requester, association, nil, &text)
	}
	return false, nil
}

// cleanPayments deletes the association's unpaid subscription payments of the
// same person created in the new subscription's period (the new one excluded).
func (s *SubscriptionRenewals) cleanPayments(ctx context.Context, tx domain.SubscriptionRenewTransaction, identity domain.ResolvedIdentity,
	created *subscriptionCreated, request SubscriptionCreateRequest) error {
	if created.start == nil || created.end == nil {
		return domain.ErrSubscriptionCreateNotPorted
	}
	// QuerySet.delete() of invoiced payments leaves their invoices: Payment.invoice points from the payment.
	ids, _, err := tx.RenewalPayments(ctx, created.association, created.first, created.last, created.tax, calendarTime(*created.start),
		calendarTime(*created.end), created.result.PaymentID, paymentStateScope(identity))
	if err != nil {
		return err
	}
	if scope := identity.ImpersonationAssociationID; scope != "" && len(ids) > 0 && identity.Effective != nil &&
		identity.Effective.Role == domain.RoleAthlete {
		// The session's person is an athlete: its own payments and those of the members it sees.
		if ids, err = tx.VisiblePayments(ctx, domain.ResourceScope{AssociationID: scope, EffectiveID: identity.Effective.ID,
			Role: identity.Effective.Role}, ids); err != nil {
			return err
		}
	}
	if len(ids) == 0 {
		return nil
	}
	actor, err := s.creates.actorAssociation(ctx, tx, request.Audit)
	if err != nil {
		return err
	}
	return tx.DeletePayments(ctx, ids, domain.CourseSubscriptionAudit{Context: request.Audit, ActorAssociation: actor, At: s.creates.now().UTC()})
}

// tags is fresh_sub.tags.set(Tags.objects.filter(tag_id__in=..., sport_association=...)).
func (s *SubscriptionRenewals) tags(ctx context.Context, tx domain.SubscriptionRenewTransaction, data domain.RequestObject,
	created *subscriptionCreated) error {
	value, present := data.Lookup("tags")
	if !present {
		return nil
	}
	size, err := pythonLen(value)
	if err != nil || size == 0 {
		return err
	}
	list, ok := value.([]any)
	if !ok {
		if _, isObject := value.(domain.RequestObject); isObject {
			return pythonFailure("0") // data['tags'][0]: KeyError(0)
		}
		// A text: tag_id__in iterates its characters, the first is no UUID.
		for _, character := range value.(string) {
			return djangoInvalidUUID(string(character))
		}
		return nil
	}
	raw := list
	if _, isText := list[0].(string); !isText {
		raw = make([]any, 0, len(list))
		for _, item := range list {
			object, ok := item.(domain.RequestObject)
			if !ok {
				return pythonSubscriptError(item)
			}
			id, present := object.Lookup("tag_id")
			if !present {
				return pythonKeyError("tag_id")
			}
			raw = append(raw, id)
		}
	}
	ids, seen := []string{}, map[string]bool{}
	for _, item := range raw {
		switch v := item.(type) {
		case nil:
		case string:
			parsed, err := paymentInfoParseUUID(v)
			if err != nil {
				return djangoInvalidUUID(v)
			}
			if id := parsed.String(); !seen[id] {
				seen[id] = true
				ids = append(ids, id)
			}
		default:
			// UUIDField.to_python: an int (a bool is one) is uuid.UUID(int=...); the rest is no UUID.
			id, valid := dto.UUIDFieldValue(v)
			if !valid {
				return djangoInvalidUUID(dto.PythonStr(v))
			}
			if !seen[id] {
				seen[id] = true
				ids = append(ids, id)
			}
		}
	}
	return tx.AddTags(ctx, created.id, ids)
}

// courses is the renewal's course loop: for each item of data['courses'] the
// association's course c['value'] (scoped_queryset(request, Course.objects), first())
// goes through add_course_to_subscription(course, fresh_sub, request.user.sport_association,
// data=None, is_athlete=False) inside the renewal's transaction. A course the
// subscription is already in is skipped. Exceptions are the view's 400 body;
// a stored event the serializer rejects is its APIException.
func (s *SubscriptionRenewals) courses(ctx context.Context, tx domain.SubscriptionRenewTransaction, identity domain.ResolvedIdentity,
	data domain.RequestObject, created *subscriptionCreated, request SubscriptionCreateRequest) error {
	value, present := data.Lookup("courses")
	if !present {
		return nil
	}
	size, err := pythonLen(value)
	if err != nil || size == 0 {
		return err
	}
	list, ok := value.([]any)
	if !ok {
		// Iterating a str or a dict gives str items: c['value'] is a TypeError.
		return pythonSubscriptError("")
	}
	if s.enrolment == nil {
		return domain.ErrSubscriptionCreateNotPorted
	}
	enrolment := tx.Enrolment()
	actor, err := s.creates.actorAssociation(ctx, tx, request.Audit)
	if err != nil {
		return err
	}
	audit := domain.CourseSubscriptionAudit{Context: request.Audit, ActorAssociation: actor, At: s.enrolment.moment()}
	for _, item := range list {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return pythonSubscriptError(item)
		}
		raw, present := object.Lookup("value")
		if !present {
			return pythonKeyError("value")
		}
		var course *domain.CourseWriteRow
		switch v := raw.(type) {
		case nil:
		case string:
			id, err := paymentInfoParseUUID(v)
			if err != nil {
				return djangoInvalidUUID(v)
			}
			if course, err = enrolment.Course(ctx, id.String(), nil); err != nil {
				return err
			}
		default:
			// Numbers (uuid.UUID(int=...)), booleans and containers are not ported.
			return domain.ErrSubscriptionCreateNotPorted
		}
		if course != nil && (!courseEnrolmentScoped(identity, course.AssociationID) || course.AssociationID == nil ||
			*course.AssociationID != created.association) {
			course = nil
		}
		if course == nil {
			return pythonAttributeError(nil, "course_type")
		}
		if !course.MultiPayments && course.Fee == nil {
			return pythonFailure("'>' not supported between instances of 'NoneType' and 'int'")
		}
		subscription, err := enrolment.Subscription(ctx, created.id, nil)
		if err != nil {
			return err
		}
		if subscription == nil {
			return domain.ErrSubscriptionCreateNotPorted
		}
		_, err = s.enrolment.addCourseToSubscription(ctx, enrolment, *course, *subscription, created.association, domain.RequestObject{}, false,
			CourseEnrolmentRequest{Audit: request.Audit}, audit, true)
		var invalid *CourseWritesEventInvalid
		switch {
		case errors.As(err, &invalid):
			return err
		case errors.Is(err, courseEnrolmentServer), errors.Is(err, ErrCourseEnrolmentNotPorted):
			// A Python exception whose text is not reproduced.
			return domain.ErrSubscriptionCreateNotPorted
		case err != nil:
			return err
		}
	}
	return nil
}

// pythonIn is `key in value` for a parsed JSON value.
func pythonIn(value any, key string) (bool, error) {
	switch v := value.(type) {
	case domain.RequestObject:
		return objectHas(v, key), nil
	case []any:
		for _, item := range v {
			if text, ok := item.(string); ok && text == key {
				return true, nil
			}
		}
		return false, nil
	case string:
		return strings.Contains(v, key), nil
	}
	return false, pythonFailure(fmt.Sprintf("argument of type '%s' is not iterable", pythonTypeLabel(value)))
}

// pythonLen is len(value) for a parsed JSON value.
func pythonLen(value any) (int, error) {
	switch v := value.(type) {
	case domain.RequestObject:
		keys := map[string]bool{}
		for _, field := range v {
			keys[field.Name] = true
		}
		return len(keys), nil
	case []any:
		return len(v), nil
	case string:
		return utf8.RuneCountInString(v), nil
	}
	return 0, pythonFailure(fmt.Sprintf("object of type '%s' has no len()", pythonTypeLabel(value)))
}

// --- subscription/associates-draft/list/approve ---

var (
	// ErrDraftApproveMissingIDs is 400 {'msg': 'bad request, missing drafts to approve.'}.
	ErrDraftApproveMissingIDs = errors.New("missing drafts to approve")
	// ErrDraftApproveNoneValid is 400 {'msg': 'Dati mancanti, iscrizione non approvata.'}.
	ErrDraftApproveNoneValid = errors.New("no valid draft")
)

// SubscriptionDraftApproveResult is "<approved> su <total> atleti approvati.".
type SubscriptionDraftApproveResult struct{ Approved, Total int }

// SubscriptionDraftApprovals ports subscription_associates_draft_approve. The
// view commits each create_subscription on its own; here the whole request is
// one transaction (a savepoint per draft) so that a refused input changes
// nothing, and the after-commit work of the approved drafts runs in order once
// it commits (subscription-renew.md).
type SubscriptionDraftApprovals struct {
	store   domain.SubscriptionDraftApproveStore
	creates *SubscriptionCreates
}

func NewSubscriptionDraftApprovals(store domain.SubscriptionDraftApproveStore, creates *SubscriptionCreates) *SubscriptionDraftApprovals {
	return &SubscriptionDraftApprovals{store: store, creates: creates}
}

// draftPythonError reports an exception the view's except block swallows.
func draftPythonError(err error) bool {
	var invalid *domain.SubscriptionCreateInvalid
	var denied *domain.PermissionDenied
	return errors.Is(err, domain.ErrSubscriptionCreateServer) || errors.Is(err, domain.ErrSubscriptionCreateDuplicate) ||
		errors.As(err, &invalid) || errors.As(err, &denied)
}

func (s *SubscriptionDraftApprovals) Approve(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionCreateRequest) (*SubscriptionDraftApproveResult, error) {
	server := domain.ErrSubscriptionCreateServer
	user := identity.ScopeUser
	if user == nil {
		return nil, server
	}
	if user.Role == domain.RoleAthlete {
		return nil, domain.ErrAssociateDraftUnauthorized
	}
	if request.Form || strings.TrimSpace(request.Group) != "" {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	request.Audit = paymentCategoryAuditContext(identity, request.Audit)
	data, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, server
	}
	drafts, present := data.Lookup("associate_import_draft_ids")
	if !present {
		return nil, ErrDraftApproveMissingIDs
	}
	detailsValue, present := data.Lookup("subscription_details")
	details, ok := detailsValue.(domain.RequestObject)
	if !present || !ok {
		return nil, server
	}
	defaults := []domain.RequestField{{Name: "type", Value: json.Number("1")}, {Name: "plan_id"}, {Name: "membership_plan_id"},
		{Name: "role", Value: json.Number("1")}}
	values := map[string]any{}
	for _, field := range defaults {
		if value, present := details.Lookup(field.Name); present {
			values[field.Name] = value
		} else {
			values[field.Name] = field.Value
		}
	}
	allValue, _ := data.Lookup("all")
	all := dto.PythonTruthy(allValue)
	ids := []string{}
	if !all {
		items, iterable := dto.PythonIterate(drafts)
		if !iterable {
			return nil, server
		}
		for _, item := range items {
			if text, isText := item.(string); isText {
				if parsed, err := paymentInfoParseUUID(text); err == nil {
					ids = append(ids, parsed.String())
				}
			}
		}
	}
	tx, err := s.store.BeginSubscriptionDraftApprove(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	count, err := tx.AssociationCount(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if count != 1 {
		return nil, server
	}
	association, err := tx.ActiveAssociationOf(ctx, user.ID)
	if err != nil || association == nil {
		return nil, errors.Join(err, server)
	}
	total, err := tx.DraftCount(ctx, *association, all, ids)
	if err != nil {
		return nil, err
	}
	rows, err := tx.ValidDrafts(ctx, *association, all, ids)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, ErrDraftApproveNoneValid
	}
	at := func() time.Time { return s.creates.now().UTC() }
	if err = tx.DeleteApprovingStatuses(ctx, *association, request.Audit, at()); err != nil {
		return nil, err
	}
	if err = tx.CreateApprovingStatus(ctx, *association, request.Audit, at()); err != nil {
		return nil, err
	}
	approved, done, crashed := 0, []*subscriptionCreated{}, false
	for _, row := range rows {
		draft, ok := approveDraftData(row.Data, values)
		if !ok {
			// The renaming raises before the try: the view stops with a 500.
			crashed = true
			break
		}
		if err = subscriptionCreateRefuse(draft, subscriptionCreateToday(s.creates.now())); err != nil {
			return nil, err
		}
		if err = tx.Savepoint(ctx); err != nil {
			return nil, err
		}
		created, err := s.creates.createSubscription(ctx, tx, identity, draft, request)
		if err != nil {
			if !draftPythonError(err) {
				return nil, err
			}
			if err = tx.RollbackSavepoint(ctx); err != nil {
				return nil, err
			}
			continue
		}
		if err = tx.ReleaseSavepoint(ctx); err != nil {
			return nil, err
		}
		// on_commit: send_notification's rows come before the draft's delete.
		subscribed, err := tx.ValidateBroadcast(ctx, user.ID, "broadcast_"+user.ID, request.Audit, at())
		if err != nil {
			return nil, err
		}
		created.validated = &subscribed
		if err = tx.DeleteDraft(ctx, row.ID, approvedDraftRepr(draft), request.Audit, at()); err != nil {
			return nil, err
		}
		approved++
		done = append(done, created)
	}
	if !crashed {
		if err = tx.DeleteApprovingStatuses(ctx, *association, request.Audit, at()); err != nil {
			return nil, err
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	for _, created := range done {
		s.creates.afterCommit(ctx, identity, created, request)
	}
	if crashed {
		return nil, server
	}
	return &SubscriptionDraftApproveResult{Approved: approved, Total: total}, nil
}

// approveDraftData is the view's renaming of draft.data; ok is false when it raises.
func approveDraftData(text *string, details map[string]any) (domain.RequestObject, bool) {
	if text == nil {
		return nil, false
	}
	parsed, err := dto.ParsePythonJSON(*text)
	if err != nil {
		return nil, false
	}
	data, ok := parsed.(domain.RequestObject)
	if !ok {
		return nil, false
	}
	associateValue, present := data.Lookup("associate")
	if !present {
		return nil, false
	}
	tutor, present := data.Lookup("associate_tutor")
	if !present {
		return nil, false
	}
	data = objectSet(data, "associate_data", associateValue)
	data = objectSet(data, "associate_tutor_data", tutor)
	data = objectSet(data, "new_user_account", domain.RequestObject{{Name: "new_member", Value: false}})
	data = objectSet(data, "signature", domain.RequestObject{{Name: "there_is_signature", Value: false}, {Name: "data", Value: ""}})
	associate, ok := associateValue.(domain.RequestObject)
	if !ok {
		return nil, false
	}
	associate = objectSet(objectSet(associate, "type", details["type"]), "role", details["role"])
	data = objectSet(objectSet(data, "associate", associate), "associate_data", associate)
	data = objectSet(data, "membership_plan_id", details["membership_plan_id"])
	data = objectSet(data, "plan_id", details["plan_id"])
	return data, true
}

// approvedDraftRepr is str(draft.data) after create_subscription: 'associate'
// and 'associate_data' (and the tutor keys) are one dict, whose
// sport_association and user create_subscription set to UUIDs.
func approvedDraftRepr(data domain.RequestObject) string {
	uuids := func(value any) any {
		object, ok := value.(domain.RequestObject)
		if !ok {
			return value
		}
		copied := append(domain.RequestObject{}, object...)
		for index, field := range copied {
			if text, isText := field.Value.(string); isText && (field.Name == "sport_association" || field.Name == "user") {
				copied[index].Value = dto.PythonUUID(text)
			}
		}
		return copied
	}
	associate, _ := data.Lookup("associate_data")
	tutor, _ := data.Lookup("associate_tutor_data")
	associate, tutor = uuids(associate), uuids(tutor)
	final := append(domain.RequestObject{}, data...)
	for index, field := range final {
		switch field.Name {
		case "associate", "associate_data":
			final[index].Value = associate
		case "associate_tutor", "associate_tutor_data":
			final[index].Value = tutor
		}
	}
	return dto.PythonStr(final)
}
