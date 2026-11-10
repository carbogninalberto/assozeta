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
	store   domain.SubscriptionRenewStore
	creates *SubscriptionCreates
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
	if user.Role != domain.RoleAssociation {
		// request.user becomes the owner of data['sport_association']['sport_association_id'];
		// the audit actor stays the requester.
		owner, err := s.renewRequesterOwner(ctx, tx, identity, request.Body)
		if err != nil {
			return nil, err
		}
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
	if err = s.subscriptionUser(ctx, tx, data, created, request); err != nil {
		return nil, renewError(err)
	}
	if err = s.tags(ctx, tx, data, created); err != nil {
		return nil, renewError(err)
	}
	if courses, present := data.Lookup("courses"); present {
		size, err := pythonLen(courses)
		if err != nil {
			return nil, renewError(err)
		}
		if size > 0 {
			// add_course_to_subscription inside the renewal is not ported.
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
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
	if identity.ImpersonationAssociationID != "" {
		// The impersonation scope's association filter is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
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
	if owner == nil {
		return nil, domain.ErrSubscriptionCreateServer
	}
	if owner.Role != domain.RoleAssociation {
		// An association whose user is not an owner account is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	return owner, nil
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
	if len(ids) == 0 {
		return nil
	}
	actor, err := s.creates.actorAssociation(ctx, tx, request.Audit)
	if err != nil {
		return err
	}
	return tx.DeletePayments(ctx, ids, domain.CourseSubscriptionAudit{Context: request.Audit, ActorAssociation: actor, At: s.creates.now().UTC()})
}

// subscriptionUser is `'user' in data and 'email' in data['user']`: the first
// user with that e-mail becomes the subscription's user (save()).
func (s *SubscriptionRenewals) subscriptionUser(ctx context.Context, tx domain.SubscriptionRenewTransaction, data domain.RequestObject,
	created *subscriptionCreated, request SubscriptionCreateRequest) error {
	value, present := data.Lookup("user")
	if !present {
		return nil
	}
	has, err := pythonIn(value, "email")
	if err != nil || !has {
		return err
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		return pythonSubscriptError(value)
	}
	raw, _ := object.Lookup("email")
	var email *string
	switch v := raw.(type) {
	case nil:
	case string:
		email = &v
	case json.Number, bool:
		text := dto.PythonStr(v)
		email = &text
	default:
		return domain.ErrSubscriptionCreateNotPorted
	}
	found, err := tx.UserByEmail(ctx, email)
	if err != nil || found == nil || *found == created.userID {
		return err
	}
	if err = tx.SetSubscriptionUser(ctx, created.id, *found); err != nil {
		return err
	}
	audit := subscriptionAudit{tx: tx, context: request.Audit, now: s.creates.now}
	association := created.association
	return audit.updated(ctx, "subscription", "Subscription", created.id, map[string][2]string{"user": {created.userID, *found}},
		&association, "direct")
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
		return domain.ErrSubscriptionCreateNotPorted
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
			return domain.ErrSubscriptionCreateNotPorted
		}
	}
	return tx.AddTags(ctx, created.id, ids)
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
