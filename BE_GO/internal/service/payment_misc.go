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
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// PaymentMisc ports payment_simulation_partial_quotes (calculate_simulation),
// payment_simulation_partial_quotes_apply (association_quote_assign) and
// payment_sign.
type PaymentMisc struct {
	store      domain.PaymentMiscStore
	writes     *PaymentWrites
	now        func() time.Time
	whitelabel string
}

func NewPaymentMisc(store domain.PaymentMiscStore, now func() time.Time, newID func() string, whitelabel string) *PaymentMisc {
	return &PaymentMisc{store: store, writes: &PaymentWrites{now: now, newID: newID}, now: now, whitelabel: whitelabel}
}

// PaymentMiscRequest carries the request values the views read.
type PaymentMiscRequest struct {
	Query       domain.RequestObject
	GroupHeader string
	Body        any
	Audit       domain.LoginAuditContext
}

// errPaymentMiscStop is a Python exception inside the views' try/except:
// what was collected (or saved) so far is kept.
var errPaymentMiscStop = errors.New("payment misc python exception")

type paymentQuotePlan struct {
	fromDay, fromMonth, toDay, toMonth int64
	previous, name, fee, id            any
}

func (p paymentQuotePlan) render() domain.RequestObject {
	return domain.RequestObject{{Name: "from_day", Value: p.fromDay}, {Name: "from_month", Value: p.fromMonth}, {Name: "to_day", Value: p.toDay},
		{Name: "to_month", Value: p.toMonth}, {Name: "previous_subscription_fee_plan", Value: dto.PaymentMiscJSON(p.previous)},
		{Name: "name", Value: dto.PaymentMiscJSON(p.name)}, {Name: "subscription_fee", Value: dto.PaymentMiscJSON(p.fee)},
		{Name: "id", Value: dto.PaymentMiscJSON(p.id)}}
}

// paymentMiscTruthy is bool() of a value json.loads produced.
func paymentMiscTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case json.Number:
		number, ok := new(big.Float).SetString(string(v))
		return !ok || number.Sign() != 0
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case domain.RequestObject:
		return len(v) > 0
	}
	return true
}

var paymentMiscIntLiteral = regexp.MustCompile(`^[+-]?[0-9]+(_[0-9]+)*$`)

// paymentMiscInt is Python's int(value): errPaymentMiscStop for the
// exceptions it raises; values Go cannot hold (or non-ASCII digits, which
// int() accepts) are not ported.
func paymentMiscInt(value any) (int64, error) {
	var integer *big.Int
	switch v := value.(type) {
	case bool:
		if v {
			return 1, nil
		}
		return 0, nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, err := strconv.ParseFloat(string(v), 64)
			if (err != nil && !errors.Is(err, strconv.ErrRange)) || math.IsInf(number, 0) || math.IsNaN(number) {
				return 0, errPaymentMiscStop
			}
			integer, _ = new(big.Float).SetFloat64(math.Trunc(number)).Int(nil)
		} else {
			integer, _ = new(big.Int).SetString(string(v), 10)
		}
	case string:
		for _, r := range v {
			if r > unicode.MaxASCII && unicode.IsDigit(r) {
				return 0, domain.ErrPaymentMiscNotPorted
			}
		}
		text := strings.TrimFunc(v, unicode.IsSpace)
		if !paymentMiscIntLiteral.MatchString(text) {
			return 0, errPaymentMiscStop
		}
		integer, _ = new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
	default:
		return 0, errPaymentMiscStop
	}
	if integer == nil || !integer.IsInt64() {
		return 0, domain.ErrPaymentMiscNotPorted
	}
	return integer.Int64(), nil
}

// paymentMiscItems is iterating subscription_fee_plans: a dictionary yields
// its keys and a string its characters (neither has .get(): a TypeError or
// AttributeError unless empty); other scalars are not iterable.
func paymentMiscItems(value any) ([]any, error) {
	switch v := value.(type) {
	case []any:
		return v, nil
	case domain.RequestObject:
		if len(v) > 0 {
			return nil, errPaymentMiscStop
		}
		return nil, nil
	case string:
		if v != "" {
			return nil, errPaymentMiscStop
		}
		return nil, nil
	}
	return nil, errPaymentMiscStop
}

// balanceYear is BalanceSheetData.get_range_from_year_and_starting_date(datetime.now(), day, month).
func paymentMiscBalanceYear(now time.Time, day, month int) (time.Time, time.Time, error) {
	if month < 1 || month > 12 || day < 1 || day > 31 {
		return time.Time{}, time.Time{}, errPaymentMiscStop
	}
	at := func(year int) (time.Time, bool) {
		moment := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		return moment, moment.Day() == day && year >= 1
	}
	current, ok := at(now.Year())
	previous, okPrevious := at(now.Year() - 1)
	if !ok || !okPrevious {
		return time.Time{}, time.Time{}, errPaymentMiscStop
	}
	start := previous
	if !now.Before(current) {
		start = current
	}
	next, ok := at(start.Year() + 1)
	if !ok {
		return time.Time{}, time.Time{}, errPaymentMiscStop
	}
	return start, next.AddDate(0, 0, -1), nil
}

// activePlans is the plan loop: everything it raises leaves nothing collected.
func paymentMiscActivePlans(plans *string, day, month int) ([]paymentQuotePlan, error) {
	var parsed any
	if plans != nil {
		value, err := dto.ParseReferenceJSON([]byte(*plans))
		if err != nil {
			return nil, err
		}
		parsed = value
	}
	items, err := paymentMiscItems(parsed)
	if err != nil {
		return nil, err
	}
	active := []paymentQuotePlan{}
	for _, item := range items {
		plan, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errPaymentMiscStop
		}
		get := func(name string, fallback any) any {
			if value, present := plan.Lookup(name); present {
				return value
			}
			return fallback
		}
		previous := get("previous_subscription_fee_plan", false)
		if previous == "" {
			continue
		}
		if !paymentMiscTruthy(get("auto_assign", false)) || !paymentMiscTruthy(get("advanced_options", false)) || !paymentMiscTruthy(previous) {
			continue
		}
		index := func(name string) (any, error) {
			if value, present := plan.Lookup(name); present {
				return value, nil
			}
			return nil, errPaymentMiscStop
		}
		number := func(name string) (int64, error) {
			value, err := index(name)
			if err != nil {
				return 0, err
			}
			return paymentMiscInt(value)
		}
		fromDay, err := number("from_day")
		if err != nil {
			return nil, err
		}
		fromMonth, err := number("from_month")
		if err != nil {
			return nil, err
		}
		if fromDay != int64(day) || fromMonth != int64(month) {
			continue
		}
		entry := paymentQuotePlan{fromDay: fromDay, fromMonth: fromMonth, previous: previous}
		if entry.toDay, err = number("to_day"); err != nil {
			return nil, err
		}
		if entry.toMonth, err = number("to_month"); err != nil {
			return nil, err
		}
		for _, field := range []struct {
			name   string
			target *any
		}{{"name", &entry.name}, {"subscription_fee", &entry.fee}, {"id", &entry.id}} {
			if *field.target, err = index(field.name); err != nil {
				return nil, err
			}
		}
		active = append(active, entry)
	}
	return active, nil
}

// paymentMiscPattern is icontains' pattern of str(value); other lookup
// values are not ported.
func paymentMiscPattern(value any) (string, error) {
	switch value.(type) {
	case string, json.Number:
		return "%" + strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(dto.PythonStr(value)) + "%", nil
	}
	return "", domain.ErrPaymentMiscNotPorted
}

var paymentMiscDecimal = regexp.MustCompile(`^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$`)

// paymentMiscAmount is DecimalField.to_python of the plan fee: the stored
// text, auditlog's str() of the raw value and the float when it is one.
func paymentMiscAmount(fee any) (string, string, *float64, error) {
	var stored, audit string
	var float *float64
	switch v := fee.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, err := strconv.ParseFloat(string(v), 64)
			if err != nil || math.IsInf(number, 0) {
				return "", "", nil, domain.ErrPaymentMiscNotPorted
			}
			float = &number
			stored, audit = dto.DecimalFromFloat9(number), dto.PythonFloatStr(number)
		} else {
			stored, audit = string(v), string(v)
		}
	case string:
		text := strings.TrimFunc(v, unicode.IsSpace)
		if !paymentMiscDecimal.MatchString(text) {
			return "", "", nil, domain.ErrPaymentMiscNotPorted
		}
		stored, audit = text, v
	default:
		return "", "", nil, domain.ErrPaymentMiscNotPorted
	}
	// numeric(9, 2): larger values fail the insert (not ported).
	number, ok := new(big.Float).SetString(stored)
	if !ok || new(big.Float).Abs(number).Cmp(big.NewFloat(9999999.995)) >= 0 {
		return "", "", nil, domain.ErrPaymentMiscNotPorted
	}
	return stored, audit, float, nil
}

func paymentMiscText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func paymentMiscOptional(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

type paymentMiscRun struct {
	tx          domain.PaymentMiscTransaction
	association *domain.PaymentSimulationAssociation
	plans       []paymentQuotePlan
	from, to    time.Time
	group       *string
}

// prepare is the view's association lookup and the selection inside the
// try: date, balance year and active plans (errPaymentMiscStop: nothing).
func (s *PaymentMisc) prepare(ctx context.Context, identity domain.ResolvedIdentity, request PaymentMiscRequest) (*paymentMiscRun, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	tx, err := s.store.BeginPaymentMisc(ctx)
	if err != nil {
		return nil, err
	}
	run := &paymentMiscRun{tx: tx, group: supplierGroup(request.GroupHeader)}
	fail := func(err error) (*paymentMiscRun, error) {
		tx.Rollback()
		return nil, err
	}
	id, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, nil)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return fail(domain.ErrPaymentMiscServer)
	}
	if err != nil {
		return fail(err)
	}
	if run.association, err = tx.SimulationAssociation(ctx, id); err != nil {
		return fail(err)
	}
	if run.association == nil {
		return fail(domain.ErrPaymentMiscServer)
	}
	now := s.now().UTC()
	day := now
	if value, present := request.Query.Lookup("date"); present {
		parsed, err := paymentStateDate(value)
		switch {
		case errors.Is(err, domain.ErrPaymentStateNotPorted):
			return fail(domain.ErrPaymentMiscNotPorted)
		case err != nil:
			return run, errPaymentMiscStop
		}
		day = *parsed
	}
	if run.from, run.to, err = paymentMiscBalanceYear(now, run.association.StartDay, run.association.StartMonth); err != nil {
		return run, err
	}
	run.plans, err = paymentMiscActivePlans(run.association.Plans, day.Day(), int(day.Month()))
	if err != nil && !errors.Is(err, errPaymentMiscStop) && !errors.Is(err, domain.ErrPaymentMiscNotPorted) {
		return fail(err)
	}
	if errors.Is(err, domain.ErrPaymentMiscNotPorted) {
		return fail(err)
	}
	return run, err
}

// quotes visits every plan's subscriptions without an existing quote, in
// the views' order; visit returning errPaymentMiscStop ends the loop.
func (r *paymentMiscRun) quotes(ctx context.Context, visit func(paymentQuotePlan, domain.PaymentSimulationSubscription, string, string,
	*float64) error) error {
	for _, plan := range r.plans {
		pattern, err := paymentMiscPattern(plan.previous)
		if err != nil {
			return err
		}
		subscriptions, err := r.tx.SimulationSubscriptions(ctx, domain.PaymentSimulationQuery{Association: r.association.ID, From: r.from,
			To: r.to, Pattern: pattern, Group: r.group})
		if err != nil {
			return err
		}
		for _, subscription := range subscriptions {
			idPattern, err := paymentMiscPattern(plan.id)
			if err != nil {
				return err
			}
			stored, audit, float, err := paymentMiscAmount(plan.fee)
			if err != nil {
				return err
			}
			exists, err := r.tx.QuoteExists(ctx, domain.PaymentQuoteCheck{User: subscription.UserID, Associate: subscription.AssociateID,
				Pattern: idPattern, Amount: stored, Group: r.group})
			if err != nil {
				return err
			}
			if exists {
				continue
			}
			if err := visit(plan, subscription, stored, audit, float); err != nil {
				return err
			}
		}
	}
	return nil
}

func paymentQuoteMeta(plan paymentQuotePlan) domain.RequestObject {
	return domain.RequestObject{{Name: "subscription_data", Value: domain.RequestObject{{Name: "subscription_fee", Value: plan.fee},
		{Name: "name", Value: plan.name}, {Name: "id", Value: plan.id}}}}
}

// Simulation follows payment_simulation_partial_quotes.
func (s *PaymentMisc) Simulation(ctx context.Context, identity domain.ResolvedIdentity, request PaymentMiscRequest) (map[string]any, error) {
	considered, emitted := []any{}, []any{}
	result := func() map[string]any {
		return map[string]any{"subscription_plans_considered": considered, "quotes_to_emit": emitted}
	}
	run, err := s.prepare(ctx, identity, request)
	if run == nil {
		return nil, err
	}
	defer run.tx.Rollback()
	if err != nil {
		return result(), nil
	}
	for _, plan := range run.plans {
		considered = append(considered, plan.render())
	}
	err = run.quotes(ctx, func(plan paymentQuotePlan, subscription domain.PaymentSimulationSubscription, _, _ string, _ *float64) error {
		if subscription.AssociateID == nil {
			// subscription.associate.first_name on None.
			return errPaymentMiscStop
		}
		quote := domain.RequestObject{
			{Name: "user", Value: strings.TrimFunc(paymentMiscText(subscription.UserFirst)+" "+paymentMiscText(subscription.UserLast), unicode.IsSpace)},
			{Name: "associate", Value: domain.RequestObject{{Name: "first_name", Value: paymentMiscOptional(subscription.AssociateFirst)},
				{Name: "last_name", Value: paymentMiscOptional(subscription.AssociateLast)}}},
			{Name: "amount", Value: dto.PaymentMiscJSON(plan.fee)}, {Name: "subject", Value: 1},
			{Name: "meta", Value: dto.PaymentMiscJSON(paymentQuoteMeta(plan))}, {Name: "message", Value: nil}}
		if subscription.UserID != run.association.OwnerID {
			quote[5].Value = s.message(run.association, plan, subscription)
		}
		emitted = append(emitted, quote)
		return nil
	})
	if err != nil && !errors.Is(err, errPaymentMiscStop) {
		return nil, err
	}
	return result(), nil
}

// message is the e-mail text calculate_simulation renders (not sent).
func (s *PaymentMisc) message(association *domain.PaymentSimulationAssociation, plan paymentQuotePlan,
	subscription domain.PaymentSimulationSubscription) string {
	return "\n                            <p>Ciao " + paymentMiscText(subscription.AssociateFirst) + " " + paymentMiscText(subscription.AssociateLast) +
		",</p>\n                            <p>Ti è stata assegnata la quota associativa \"" + dto.PythonStr(plan.name) + "\" con importo " +
		dto.PythonStr(plan.fee) + " €.</p>\n                            <br>\n                            <p>Per maggiori informazioni, visita il tuo profilo su " +
		s.whitelabel + ".</p>\n    \n                            <p>Cordialmente, <br />\n                             " +
		paymentMiscText(association.Denomination) + "</p>\n                        "
}

// Apply follows payment_simulation_partial_quotes_apply. Every quote is a
// Payment.objects.create with its audited create entry; Django commits each
// one, so a Python exception keeps the earlier ones. Quotes for users other
// than the association owner send an e-mail: not ported, refused before
// anything is committed.
func (s *PaymentMisc) Apply(ctx context.Context, identity domain.ResolvedIdentity, request PaymentMiscRequest) error {
	run, err := s.prepare(ctx, identity, request)
	if run == nil {
		return err
	}
	defer run.tx.Rollback()
	if err != nil {
		return run.tx.Commit()
	}
	tx := run.tx
	err = run.quotes(ctx, func(plan paymentQuotePlan, subscription domain.PaymentSimulationSubscription, stored, audit string,
		float *float64) error {
		other := subscription.UserID != run.association.OwnerID
		if other && subscription.AssociateID != nil {
			// render_to_string and send_email_html: not ported.
			return domain.ErrPaymentMiscNotPorted
		}
		account, err := tx.DefaultBankAccount(ctx, run.association.ID)
		if err != nil {
			return err
		}
		group, err := s.writes.createGroup(ctx, tx, request.GroupHeader)
		if errors.Is(err, domain.ErrPaymentWritesServer) {
			return errPaymentMiscStop
		}
		if errors.Is(err, domain.ErrPaymentWritesNotPorted) {
			return domain.ErrPaymentMiscNotPorted
		}
		if err != nil {
			return err
		}
		user, association := subscription.UserID, run.association.ID
		m := &paymentMemory{row: domain.PaymentWriteRow{ID: s.writes.newID(), Type: "default", Subject: 1, CreationDate: s.writes.at(),
			UserID: &user, AssociateID: subscription.AssociateID, AssociationID: &association, AccountID: account, GroupID: group},
			jsonAudit: map[string]string{}}
		if float != nil {
			m.setFloat(*float)
		} else {
			m.row.Amount, m.amountAudit, m.amountShown = stored, &audit, &stored
		}
		m.setJSON("meta", paymentQuoteMeta(plan), &m.row.Meta)
		if err := s.writes.defaults(ctx, tx, m); err != nil {
			return err
		}
		if err := tx.InsertPaymentRow(ctx, m.row); err != nil {
			return err
		}
		created, err := s.writes.values(ctx, tx, m)
		if err != nil {
			return err
		}
		if err := s.writes.audit(ctx, tx, identity, request.Audit, m.row.ID, 0, createdPaymentChanges(created), m.row.AssociationID); err != nil {
			return err
		}
		if other {
			// subscription.associate.first_name on None while rendering the e-mail.
			return errPaymentMiscStop
		}
		return nil
	})
	if err != nil && !errors.Is(err, errPaymentMiscStop) {
		return err
	}
	return tx.Commit()
}

// paymentSignUUID is is_valid_uuid's ValidationError detail for any value.
func paymentSignUUID(value any) (string, error) {
	if value == nil {
		return "", &dto.MessageReadUUIDError{Detail: "one of the hex, bytes, bytes_le, fields, or int arguments must be given"}
	}
	if text, ok := value.(string); ok {
		return messageReadsParseUUID(text)
	}
	return "", pythonUUIDValidation(value)
}

// PaymentSignRequired is SignatureRequestSerializer.validate's error code.
const PaymentSignRequired = "signature-required"

// Sign follows payment_sign without a signature to store (storing one
// creates a Signature, saves the payment and renders the compensation PDF
// synchronously: not ported).
func (s *PaymentMisc) Sign(ctx context.Context, identity domain.ResolvedIdentity, request PaymentMiscRequest) (map[string]any, error) {
	object, ok := request.Body.(domain.RequestObject)
	if !ok {
		// data.keys() on a value without keys().
		return nil, domain.ErrPaymentMiscServer
	}
	id, hasID := object.Lookup("payment_id")
	signature, hasSignature := object.Lookup("signature")
	if !hasID || !hasSignature || len(object) != 2 {
		return nil, domain.ErrPaymentMiscBadRequest
	}
	key, err := paymentSignUUID(id)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginPaymentMisc(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	found, err := tx.SignPayment(ctx, key, supplierGroup(request.GroupHeader), paymentStateScope(identity))
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, &dto.MessageReadUUIDError{Detail: "payment not found"}
	}
	fields, ok := signature.(domain.RequestObject)
	if signature == nil {
		return nil, &domain.PaymentMiscValidation{Fields: map[string][]string{"non_field_errors": {"no-data"}}}
	}
	if !ok {
		return nil, &domain.PaymentMiscValidation{Fields: map[string][]string{"non_field_errors": {"dictionary:" + dto.PaymentMiscTypeName(signature)}}}
	}
	errs := map[string][]string{}
	flag := false
	if value, present := fields.Lookup("there_is_signature"); present {
		if value == nil {
			errs["there_is_signature"] = []string{dto.PaymentCategoryValidationNull}
		} else if parsed, valid := dto.DRFBoolean(value); valid {
			flag = parsed
		} else {
			errs["there_is_signature"] = []string{dto.PaymentCategoryValidationBoolean}
		}
	}
	data := ""
	if value, present := fields.Lookup("data"); !present {
		errs["data"] = []string{dto.PaymentCategoryValidationRequired}
	} else if value != nil {
		text, codes := dto.PaymentMiscCharField(value)
		if len(codes) == 1 && codes[0] == dto.PaymentCategoryValidationBlank {
			codes = nil
		}
		if len(codes) > 0 {
			errs["data"] = codes
		}
		data = text
	}
	if len(errs) > 0 {
		return nil, &domain.PaymentMiscValidation{Fields: errs}
	}
	if flag && data == "" {
		return nil, &domain.PaymentMiscValidation{Fields: map[string][]string{"data": {PaymentSignRequired}}}
	}
	if flag {
		return nil, domain.ErrPaymentMiscNotPorted
	}
	return map[string]any{"status": "success"}, nil
}
