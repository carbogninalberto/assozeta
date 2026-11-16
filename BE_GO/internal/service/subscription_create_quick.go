package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
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

// subscriptionQuickID is Associate.objects.get(associate_id=value)'s
// UUIDField.to_python: a text through uuid.UUID(hex=...) and a number through
// uuid.UUID(int=...) (a bool included); a malformed text, a float, a negative
// number or one past 128 bits is a ValidationError and None or a container
// finds no row: the 500 page.
func subscriptionQuickID(value any) (string, error) {
	if id, ok := renewalSourceKey(value); ok {
		return id, nil
	}
	return "", domain.ErrSubscriptionCreateServer
}

// quickDecimal is DecimalField(9, 2)'s reading of the fee a plan or the
// association holds: the exact decimal the field's to_python returns (a text
// through Decimal(), an int exactly, a float through Context(prec=9)
// .create_decimal_from_float), as coefficient digits and a power of ten.
type quickDecimal struct {
	digits   string
	exponent int
	negative bool
}

// quickDecimalGrammar is decimal.Decimal()'s finite grammar after the
// underscores are removed and the decimal digits mapped to ASCII.
var quickDecimalGrammar = regexp.MustCompile(`^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$`)

// quickDecimalText is Decimal(text) of DecimalField.to_python when the fee is a
// text: the C decimal's syntax (surrounding whitespace and every underscore
// removed, any script's decimal digits) and no context rounding. ok is false
// for what to_python rejects: a syntax Decimal() raises on and a non-finite
// value, whose ValidationError the quick view does not catch.
func quickDecimalText(text string) (quickDecimal, bool) {
	value := dto.PythonASCIIDigits(strings.ReplaceAll(dto.PythonStrip(text), "_", ""))
	switch strings.ToLower(value) {
	case "inf", "+inf", "infinity", "+infinity", "-inf", "-infinity", "nan", "+nan", "-nan":
		return quickDecimal{}, false
	}
	match := quickDecimalGrammar.FindStringSubmatch(value)
	if match == nil {
		return quickDecimal{}, false
	}
	whole, fraction := match[2], match[3]
	if whole == "" {
		whole = "0"
	}
	if match[4] != "" {
		fraction = match[4]
	}
	exponent := 0
	if match[5] != "" {
		parsed, err := strconv.Atoi(match[5])
		if err != nil {
			// Decimal() rejects an exponent beyond its own limits: the 500 page.
			return quickDecimal{}, false
		}
		exponent = parsed
	}
	return quickDecimal{digits: whole + fraction, exponent: exponent - len(fraction), negative: match[1] == "-"}, true
}

// quickDecimalOf is DecimalField.to_python of the fee: a text, an int (a bool
// included) or a float (Context.create_decimal_from_float); ok is false for
// what to_python rejects (None, containers and the non-finite values).
func quickDecimalOf(value any) (quickDecimal, bool) {
	switch v := value.(type) {
	case string:
		return quickDecimalText(v)
	case bool:
		if v {
			return quickDecimal{digits: "1"}, true
		}
		return quickDecimal{digits: "0"}, true
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			integer, ok := new(big.Int).SetString(text, 10)
			if !ok {
				return quickDecimal{}, false
			}
			negative := integer.Sign() < 0
			return quickDecimal{digits: new(big.Int).Abs(integer).String(), negative: negative}, true
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil {
			if failure, ok := err.(*strconv.NumError); !ok || failure.Err != strconv.ErrRange {
				return quickDecimal{}, false
			}
		}
		return quickDecimalFloat(number)
	case float64:
		return quickDecimalFloat(v)
	}
	return quickDecimal{}, false
}

func quickDecimalFloat(value float64) (quickDecimal, bool) {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return quickDecimal{}, false
	}
	// Context(prec=9).create_decimal_from_float: nine significant digits, halves even.
	parsed, ok := quickDecimalText(dto.DecimalFromFloat9(value))
	if !ok {
		return quickDecimal{}, false
	}
	parsed.negative = math.Signbit(value) && parsed.digits != "0"
	return parsed, true
}

// quickNumericText is the numeric(9,2) value PostgreSQL stores: two decimals,
// halves away from zero. ok is false when the value does not fit the column
// (numeric field overflow).
func (d quickDecimal) numericText() (string, bool) {
	coefficient, ok := new(big.Int).SetString(d.digits, 10)
	if !ok {
		return "", false
	}
	scale := d.exponent + 2
	switch {
	case scale < 0:
		// Only the kept digits take part: a value below half a cent rounds to
		// zero without materialising a huge power of ten.
		digits := len(strings.TrimLeft(d.digits, "0"))
		if coefficient.Sign() == 0 || -scale > digits {
			coefficient = big.NewInt(0)
			break
		}
		divisor := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(-scale)), nil)
		quotient, remainder := new(big.Int).QuoRem(coefficient, divisor, new(big.Int))
		if new(big.Int).Mul(remainder, big.NewInt(2)).Cmp(divisor) >= 0 {
			quotient.Add(quotient, big.NewInt(1))
		}
		coefficient = quotient
	case scale > 0:
		if coefficient.Sign() != 0 && len(coefficient.String())+scale > 10 {
			return "", false // past numeric(9,2)'s seven integer digits
		}
		coefficient.Mul(coefficient, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(scale)), nil))
	}
	if coefficient.Cmp(big.NewInt(1000000000)) >= 0 {
		return "", false // numeric(9,2) holds at most 9999999.99
	}
	text := coefficient.String()
	for len(text) <= 2 {
		text = "0" + text
	}
	sign := ""
	if d.negative && coefficient.Sign() != 0 {
		sign = "-"
	}
	return sign + text[:len(text)-2] + "." + text[len(text)-2:], true
}

// subscriptionQuickAmount is Payment.objects.create(amount=fee) of
// create_quick_subscription: the numeric(9,2) text the row stores and str() of
// the in-memory value auditlog renders (the fee as the plan holds it).
func subscriptionQuickAmount(value any) (stored, rendered string, err error) {
	rendered = dto.PythonStr(value)
	number, ok := quickDecimalOf(value)
	if !ok {
		// Decimal(amount) raises for a container or None: Payment.save does not
		// handle it, and the column is NOT NULL.
		return "", "", domain.ErrSubscriptionCreateServer
	}
	if stored, ok = number.numericText(); !ok {
		return "", "", domain.ErrSubscriptionCreateServer
	}
	return stored, rendered, nil
}

// quickPlanItem is value['key'] on a plan of subscription_fee_plans.
func quickPlanItem(plan any, key string) (any, error) {
	object, ok := plan.(domain.RequestObject)
	if !ok {
		return nil, pythonSubscriptError(plan)
	}
	value, present := object.Lookup(key)
	if !present {
		return nil, pythonKeyError(key)
	}
	return value, nil
}

// quickPlanMeta is the payment meta of a plan: its subscription_fee, name and
// id in that order.
func quickPlanMeta(plan any) (string, error) {
	values := domain.RequestObject{}
	for _, key := range []string{"subscription_fee", "name", "id"} {
		value, err := quickPlanItem(plan, key)
		if err != nil {
			return "", err
		}
		values = append(values, domain.RequestField{Name: key, Value: value})
	}
	return string(dto.PythonJSONDumps(domain.RequestObject{{Name: "subscription_data", Value: values}})), nil
}

// quickPlanFirst is available[0]: the first item of a list, the first character
// of a text (Python code points); a JSON object has no 0 key.
func quickPlanFirst(available any) (any, error) {
	switch v := available.(type) {
	case []any:
		return v[0], nil
	case string:
		for _, point := range v {
			return string(point), nil
		}
		return nil, domain.ErrSubscriptionCreateUnreachable // not empty (len checked)
	}
	// available[0] on a dict: KeyError(0).
	return nil, domain.ErrSubscriptionCreateServer
}

// subscriptionQuickRejected is ValidationError(str(e)): DRF renders the message
// as a one-item list.
func subscriptionQuickRejected(err error) error {
	var failure *domain.SubscriptionCreateFailure
	if errors.As(err, &failure) {
		return &domain.SubscriptionCreateRejected{Messages: []string{failure.Text}}
	}
	return err
}

// subscriptionQuickFee is resolve_subscription_fee_and_meta: the association's
// fee, or with several plans the one whose id equals plan_id (Python's ==), else
// the first plan's fee with the last plan's data.
func subscriptionQuickFee(settings *domain.SubscriptionCreateSettings, data domain.RequestObject) (any, *string, error) {
	var fee any = settings.SubscriptionFee
	if !settings.MultipleSubscriptionFee {
		return fee, nil, nil
	}
	parsed, err := dto.ParsePythonJSON(string(settings.SubscriptionPlans))
	if err != nil {
		// jsonb text is valid JSON, within the digit and nesting limits of the Python writers.
		return nil, nil, domain.ErrSubscriptionCreateUnreachable
	}
	size, err := pythonLen(parsed)
	if err != nil {
		// len(subscription_fee_plans) raises for a number, a boolean or None.
		return nil, nil, domain.ErrSubscriptionCreateServer
	}
	// plan = available[0] if len(available) > 0 else None
	var plan any
	if size > 0 {
		if plan, err = quickPlanFirst(parsed); err != nil {
			return nil, nil, err
		}
	}
	available, _ := dto.PythonIterate(parsed)
	planID, hasPlan := data.Lookup("plan_id")
	for _, item := range available {
		plan = item
		if !hasPlan {
			continue
		}
		id, err := quickPlanItem(item, "id")
		if err != nil {
			// An uncaught exception in the loop: the 500 page.
			return nil, nil, err
		}
		if !pythonJSONEqual(id, planID) {
			continue
		}
		value, err := quickPlanItem(item, "subscription_fee")
		if err != nil {
			return nil, nil, err
		}
		text, err := quickPlanMeta(item)
		if err != nil {
			return nil, nil, err
		}
		return value, &text, nil
	}
	// Not found: the first plan's fee with the last iterated plan's data.
	if !dto.PythonTruthy(plan) {
		return fee, nil, nil
	}
	first, err := quickPlanFirst(parsed)
	if err != nil {
		return nil, nil, subscriptionQuickRejected(err)
	}
	value, err := quickPlanItem(first, "subscription_fee")
	if err != nil {
		return nil, nil, subscriptionQuickRejected(err)
	}
	text, err := quickPlanMeta(plan)
	if err != nil {
		return nil, nil, subscriptionQuickRejected(err)
	}
	return value, &text, nil
}

// subscriptionQuickCertificate is get_latest_medical_certificate: the first
// subscription's certificate, replaced by any whose expiration date is later
// (or by the first one that has a date), in the subscriptions' order.
func subscriptionQuickCertificate(certificates []domain.SubscriptionQuickCertificate) *string {
	var best *domain.SubscriptionQuickCertificate
	for index := range certificates {
		item := &certificates[index]
		switch {
		case best == nil:
			best = item
		case item.Expiration != nil && (best.Expiration == nil || item.Expiration.After(*best.Expiration)):
			best = item
		}
	}
	if best == nil {
		return nil
	}
	return &best.ID
}

// quickMember is _handle_quick_subscription for an account that is not an
// association: the body's association and associate are looked up (a miss or a
// malformed id is the 500 page), then the answer is 403, since the quick
// re-subscription is for the association's own owner.
func (s *SubscriptionCreates) quickMember(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject, group *string) error {
	// In an association-scoped session scoped_queryset keeps both lookups to
	// the session's association (the request guard has checked the ids).
	scope := identity.ImpersonationAssociationID
	named, _ := data.Lookup("sport_association")
	association, err := subscriptionQuickID(named)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginSubscriptionCreate(ctx)
	if err != nil {
		return err
	}
	tx.SetGroup(group)
	defer tx.Rollback()
	found, err := tx.QuickAssociationExists(ctx, association)
	if err != nil {
		return err
	}
	if !found || (scope != "" && association != scope) {
		return domain.ErrSubscriptionCreateServer // DoesNotExist
	}
	value, _ := data.Lookup("associate")
	id, err := subscriptionQuickID(value)
	if err != nil {
		return err
	}
	associate, err := tx.QuickAssociate(ctx, id)
	if err != nil {
		return err
	}
	if associate == nil || (scope != "" && (associate.AssociationID == nil || *associate.AssociationID != scope)) {
		return domain.ErrSubscriptionCreateServer // DoesNotExist
	}
	return &domain.PermissionDenied{Detail: "User not allowed."}
}

// quick follows _handle_quick_subscription for an owner (or a collaborator acting as one).
func (s *SubscriptionCreates) quick(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject,
	request SubscriptionCreateRequest, group *string) (*SubscriptionCreateResult, error) {
	scope := identity.ImpersonationAssociationID
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
	tx.SetGroup(group)
	defer tx.Rollback()
	// The association row is locked (select_for_update) with the owner's settings.
	settings, count, err := tx.Settings(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if count != 1 {
		// Django raises AttributeError on None: the 500 page.
		return nil, domain.ErrSubscriptionCreateServer
	}
	association := settings.AssociationID
	if scope != "" && association != scope {
		// The session does not find the owner's association: .first() is None.
		return nil, domain.ErrSubscriptionCreateServer
	}
	associate, err := tx.QuickAssociate(ctx, id)
	if err != nil {
		return nil, err
	}
	if associate == nil || (scope != "" && (associate.AssociationID == nil || *associate.AssociationID != scope)) {
		return nil, domain.ErrSubscriptionCreateServer // DoesNotExist
	}
	// The quick re-subscription only uses the association's own members, for
	// its own owner (an anonymous request has no requester).
	if request.Anonymous || associate.AssociationID == nil || *associate.AssociationID != association {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
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
	audit := subscriptionAudit{tx: tx, context: request.Audit, now: s.now, group: group, sentGroup: request.Group,
		inserted: map[string]bool{}}
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
	medical := subscriptionQuickCertificate(certificates)
	if associate.UserID == nil {
		return nil, domain.ErrSubscriptionCreateServer // associate.user.user_id on None
	}
	now := s.now().UTC()
	today := calendarTime(subscriptionCreateDay(now))
	duplicate, err := tx.Existing(ctx, association, associate.FirstName, associate.LastName, associate.TaxCode,
		subscriptionCreateDay(today.AddDate(0, 0, -1)), subscriptionCreateDay(today.AddDate(0, 0, 1)), nil)
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
