package service

import (
	"context"
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// The quick re-subscription (subscription/add?m=only_associate):
// _handle_quick_subscription and SubscriptionService.create_quick_subscription
// (subscription-create.md).

// subscriptionQuickDecimal is a fee text the DecimalField(9, 2) stores unchanged.
var subscriptionQuickDecimal = regexp.MustCompile(`^-?\d{1,7}(\.\d{1,2})?$`)

// subscriptionQuickID is Associate.objects.get(associate_id=value)'s
// UUIDField.to_python: a malformed text, None and containers are the 500
// page; numbers (uuid.UUID(int=...)) are not ported.
func subscriptionQuickID(value any) (string, error) {
	switch v := value.(type) {
	case string:
		id, err := paymentInfoParseUUID(v)
		if err != nil {
			return "", domain.ErrSubscriptionCreateServer
		}
		return id.String(), nil
	case json.Number, bool:
		return "", domain.ErrSubscriptionCreateNotPorted
	}
	return "", domain.ErrSubscriptionCreateServer
}

// subscriptionQuickEqual is Python's == for plan ids; shapes it does not
// decide reliably are not ported.
func subscriptionQuickEqual(left, right any) (bool, error) {
	switch l := left.(type) {
	case nil:
		return right == nil, nil
	case string:
		r, ok := right.(string)
		if ok {
			return l == r, nil
		}
		switch right.(type) {
		case nil, json.Number:
			return false, nil
		}
	case json.Number:
		switch r := right.(type) {
		case json.Number:
			a, okA := new(big.Float).SetString(string(l))
			b, okB := new(big.Float).SetString(string(r))
			if !okA || !okB {
				return false, domain.ErrSubscriptionCreateNotPorted
			}
			return a.Cmp(b) == 0, nil
		case nil, string:
			return false, nil
		}
	}
	return false, domain.ErrSubscriptionCreateNotPorted
}

// subscriptionQuickAmount is the in-memory fee: the stored decimal text and
// str() of the value auditlog renders.
func subscriptionQuickAmount(value any) (stored, rendered string, err error) {
	switch v := value.(type) {
	case string:
		if !subscriptionQuickDecimal.MatchString(v) {
			return "", "", domain.ErrSubscriptionCreateNotPorted
		}
		return v, v, nil
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			if !subscriptionQuickDecimal.MatchString(text) {
				return "", "", domain.ErrSubscriptionCreateNotPorted
			}
			return text, text, nil
		}
		number, parseErr := strconv.ParseFloat(text, 64)
		if parseErr != nil {
			return "", "", domain.ErrSubscriptionCreateNotPorted
		}
		rendered := dto.PythonFloatText(number)
		if !subscriptionQuickDecimal.MatchString(rendered) {
			return "", "", domain.ErrSubscriptionCreateNotPorted
		}
		return rendered, rendered, nil
	}
	return "", "", domain.ErrSubscriptionCreateNotPorted
}

// subscriptionQuickFee is resolve_subscription_fee_and_meta.
func subscriptionQuickFee(settings *domain.SubscriptionCreateSettings, data domain.RequestObject) (any, *string, error) {
	var fee any = settings.SubscriptionFee
	if !settings.MultipleSubscriptionFee {
		return fee, nil, nil
	}
	parsed, err := dto.ParsePythonJSON(string(settings.SubscriptionPlans))
	if err != nil {
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	}
	available, ok := parsed.([]any)
	if !ok {
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	}
	planID, hasPlan := data.Lookup("plan_id")
	meta := func(plan domain.RequestObject) (*string, error) {
		values := domain.RequestObject{}
		for _, key := range []string{"subscription_fee", "name", "id"} {
			value, present := plan.Lookup(key)
			if !present {
				return nil, domain.ErrSubscriptionCreateNotPorted
			}
			values = append(values, domain.RequestField{Name: key, Value: value})
		}
		text := string(dto.PythonJSONDumps(domain.RequestObject{{Name: "subscription_data", Value: values}}))
		return &text, nil
	}
	var last any
	for _, item := range available {
		last = item
		plan, isPlan := item.(domain.RequestObject)
		if !isPlan {
			return nil, nil, domain.ErrSubscriptionCreateNotPorted
		}
		if !hasPlan {
			continue
		}
		id, present := plan.Lookup("id")
		if !present {
			return nil, nil, domain.ErrSubscriptionCreateServer // plan['id']: KeyError
		}
		equal, err := subscriptionQuickEqual(id, planID)
		if err != nil {
			return nil, nil, err
		}
		if equal {
			value, present := plan.Lookup("subscription_fee")
			if !present {
				return nil, nil, domain.ErrSubscriptionCreateServer
			}
			text, err := meta(plan)
			if err != nil {
				return nil, nil, domain.ErrSubscriptionCreateServer
			}
			return value, text, nil
		}
	}
	// Not found: the first plan's fee with the last iterated plan's data (a truthy dict).
	if plan, isPlan := last.(domain.RequestObject); isPlan && len(plan) > 0 {
		first := available[0].(domain.RequestObject)
		value, present := first.Lookup("subscription_fee")
		if !present {
			// ValidationError(str(KeyError)) is not ported.
			return nil, nil, domain.ErrSubscriptionCreateNotPorted
		}
		text, err := meta(plan)
		if err != nil {
			return nil, nil, err
		}
		return value, text, nil
	}
	if last != nil {
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	}
	return fee, nil, nil
}

// subscriptionQuickCertificate is get_latest_medical_certificate: the first
// certificate, replaced by any with a later (or a first) expiration date. A
// result that depends on the unordered subscriptions is not ported.
func subscriptionQuickCertificate(certificates []domain.SubscriptionQuickCertificate) (*string, error) {
	if len(certificates) == 0 {
		return nil, nil
	}
	var best *domain.SubscriptionQuickCertificate
	ties := map[string]bool{}
	for i := range certificates {
		item := &certificates[i]
		if item.Expiration == nil {
			continue
		}
		switch {
		case best == nil || item.Expiration.After(*best.Expiration):
			best, ties = item, map[string]bool{item.ID: true}
		case item.Expiration.Equal(*best.Expiration):
			ties[item.ID] = true
		}
	}
	if best != nil {
		if len(ties) > 1 {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		return &best.ID, nil
	}
	for _, item := range certificates[1:] {
		if item.ID != certificates[0].ID {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
	}
	return &certificates[0].ID, nil
}

// quick follows _handle_quick_subscription for an owner (or a collaborator acting as one).
func (s *SubscriptionCreates) quick(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject,
	request SubscriptionCreateRequest) (*SubscriptionCreateResult, error) {
	if identity.ImpersonationAssociationID != "" {
		// scoped_queryset's association filter on the associate is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	value, _ := data.Lookup("associate")
	id, err := subscriptionQuickID(value)
	if err != nil {
		return nil, err
	}
	user := identity.ScopeUser
	tx, err := s.store.BeginSubscriptionCreate(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	// The association row is locked (select_for_update) with the owner's settings.
	settings, count, err := tx.Settings(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if count != 1 {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	association := settings.AssociationID
	associate, err := tx.QuickAssociate(ctx, id)
	if err != nil {
		return nil, err
	}
	if associate == nil {
		return nil, domain.ErrSubscriptionCreateServer // DoesNotExist
	}
	fee, meta, err := subscriptionQuickFee(settings, data)
	if err != nil {
		return nil, err
	}
	amount, rendered, err := subscriptionQuickAmount(fee)
	if err != nil {
		return nil, err
	}
	category, err := tx.Category(ctx, settings.DefaultCoursesCategory, association)
	if err != nil {
		return nil, err
	}
	if category == nil {
		// Payment.save() looks the named category up.
		if category, err = tx.Category(ctx, nil, association); err != nil {
			return nil, err
		}
	}
	account, err := tx.DefaultBankAccount(ctx, association)
	if err != nil {
		return nil, err
	}
	audit := subscriptionAudit{tx: tx, context: request.Audit, now: s.now}
	ownAssociation := &association
	paymentID := uuid.NewString()
	if err = tx.InsertPayment(ctx, domain.SubscriptionCreatePayment{ID: paymentID, AssociationID: association, AssociateID: associate.ID,
		UserID: user.ID, Amount: amount, CategoryID: category, AccountID: account, Meta: meta, Created: s.now().UTC()}); err != nil {
		return nil, err
	}
	if err = audit.created(ctx, "payment", "Payment", "application_payment", "payment_id", paymentID,
		map[string][2]string{"amount": {"None", rendered}}, ownAssociation, "direct"); err != nil {
		return nil, err
	}
	certificates, err := tx.QuickCertificates(ctx, associate.ID)
	if err != nil {
		return nil, err
	}
	medical, err := subscriptionQuickCertificate(certificates)
	if err != nil {
		return nil, err
	}
	if associate.UserID == nil {
		return nil, domain.ErrSubscriptionCreateServer // associate.user.user_id on None
	}
	now := s.now().UTC()
	today := calendarTime(subscriptionCreateDay(now))
	duplicate, err := tx.Existing(ctx, association, associate.FirstName, associate.LastName, associate.TaxCode,
		subscriptionCreateDay(today.AddDate(0, 0, -1)), subscriptionCreateDay(today.AddDate(0, 0, 1)))
	if err != nil {
		return nil, err
	}
	if duplicate != nil {
		return nil, domain.ErrSubscriptionCreateDuplicate
	}
	rule := domain.CalendarRule{StartDay: settings.StartDay, StartMonth: settings.StartMonth, CustomEnd: settings.CustomEnd,
		EndDay: settings.EndDay, EndMonth: settings.EndMonth}
	dates, err := ResolveSubscriptionDates(ctx, now, domain.SubscriptionDateRule{Season: rule, Duration: settings.SubscriptionDuration},
		domain.SubscriptionDates{})
	if err != nil {
		return nil, subscriptionCreateCalendar(err)
	}
	planID, _ := data.Lookup("plan_id")
	subscriptionID := uuid.NewString()
	if err = tx.InsertSubscription(ctx, domain.SubscriptionCreateRow{ID: subscriptionID, AssociationID: association, AssociateID: associate.ID,
		UserID: *associate.UserID, Type: 2, Role: 1, Status: 1, MedicalID: medical, PaymentID: &paymentID,
		Meta: string(dto.PythonJSONDumps(domain.RequestObject{{Name: "plan_id", Value: planID}})), Number: "0", Created: now,
		Start: dates.Start, End: dates.End}); err != nil {
		return nil, err
	}
	if err = audit.created(ctx, "subscription", "Subscription", "application_subscription", "subscription_id", subscriptionID, nil,
		ownAssociation, "direct"); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	name := strings.TrimSpace(subscriptionCreateValue(associate.FirstName) + " " + subscriptionCreateValue(associate.LastName))
	s.afterCommit(ctx, identity, &subscriptionCreated{id: subscriptionID, association: association, associateName: name}, request)
	return &SubscriptionCreateResult{PaymentID: &paymentID}, nil
}
