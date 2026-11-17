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

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/google/uuid"
)

// PaymentMisc ports payment_simulation_partial_quotes (calculate_simulation),
// payment_simulation_partial_quotes_apply (association_quote_assign) and
// payment_sign.
type PaymentMisc struct {
	store      domain.PaymentMiscStore
	writes     *PaymentWrites
	now        func() time.Time
	whitelabel string
	// mails renders and publishes the apply's send_email_html calls.
	mails *EmailRenderRoutes
	// printer runs payment_sign's print_document_compensation.apply.
	printer *InvoiceSyncPrint
	// configured runs send_email_html's CommunicationConfiguration send in this
	// process (the Timer thread); nil refuses an apply that needs it.
	configured *SendMailAsync
}

// WithConfiguredMail sets the in-process send_email_html of associations with
// a CommunicationConfiguration.
func (s *PaymentMisc) WithConfiguredMail(mail *SendMailAsync) *PaymentMisc {
	s.configured = mail
	return s
}

// WithMail sets the e-mail renderer and Celery producer of the apply.
func (s *PaymentMisc) WithMail(mails *EmailRenderRoutes) *PaymentMisc {
	s.mails = mails
	return s
}

// WithCompensationPrint sets the eager print runner of payment_sign.
func (s *PaymentMisc) WithCompensationPrint(printer *InvoiceSyncPrint) *PaymentMisc {
	s.printer = printer
	return s
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
	// Language is the request's active language (render_to_string).
	Language string
	// Authorization is the raw Authorization header (nil when absent).
	Authorization *string
}

// errPaymentMiscStop is a Python exception inside the views' try/except:
// what was collected (or saved) so far is kept.
var errPaymentMiscStop = errors.New("payment misc python exception")

type paymentQuotePlan struct {
	// The day and month bounds are Python int() results: unbounded integers.
	fromDay, fromMonth, toDay, toMonth *big.Int
	previous, name, fee, id            any
}

// paymentMiscNumber is a Python int as the JSON renderer writes it: its digits.
func paymentMiscNumber(value *big.Int) json.Number { return json.Number(value.String()) }

func (p paymentQuotePlan) render() domain.RequestObject {
	return domain.RequestObject{{Name: "from_day", Value: paymentMiscNumber(p.fromDay)},
		{Name: "from_month", Value: paymentMiscNumber(p.fromMonth)}, {Name: "to_day", Value: paymentMiscNumber(p.toDay)},
		{Name: "to_month", Value: paymentMiscNumber(p.toMonth)}, {Name: "previous_subscription_fee_plan", Value: dto.PaymentMiscJSON(p.previous)},
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
// exceptions it raises, the value itself for every integer (Python ints are
// unbounded: a 30-digit day bound converts and compares like any other).
func paymentMiscInt(value any) (*big.Int, error) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewInt(1), nil
		}
		return big.NewInt(0), nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, err := strconv.ParseFloat(string(v), 64)
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return nil, errPaymentMiscStop
			}
			if math.IsInf(number, 0) || math.IsNaN(number) {
				// int(inf) raises OverflowError, int(nan) ValueError.
				return nil, errPaymentMiscStop
			}
			// int(float) is the exact integer part of the double.
			integer, _ := new(big.Float).SetFloat64(math.Trunc(number)).Int(nil)
			return integer, nil
		}
		integer, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return nil, errPaymentMiscStop
		}
		return integer, nil
	case string:
		// int() reads the decimal digits of any script.
		text := dto.PythonASCIIDigits(strings.TrimFunc(v, unicode.IsSpace))
		if !paymentMiscIntLiteral.MatchString(text) {
			return nil, errPaymentMiscStop
		}
		integer, ok := new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
		if !ok {
			return nil, errPaymentMiscStop
		}
		return integer, nil
	}
	return nil, errPaymentMiscStop
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
		number := func(name string) (*big.Int, error) {
			value, err := index(name)
			if err != nil {
				return nil, err
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
		if fromDay.Cmp(big.NewInt(int64(day))) != 0 || fromMonth.Cmp(big.NewInt(int64(month))) != 0 {
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

// paymentMiscPattern is icontains' pattern of str(value): strings and numbers
// as written, booleans, lists and objects as their Python repr. Those are
// every value json.loads produces but None, whose lookup raises ValueError
// (Cannot use None as a query value): a Python exception of the caller's try.
func paymentMiscPattern(value any) (string, error) {
	switch value.(type) {
	case string, json.Number, bool, []any, domain.RequestObject:
		return "%" + strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(dto.PythonStr(value)) + "%", nil
	case nil:
		return "", errPaymentMiscStop
	}
	return "", domain.ErrPaymentMiscUnreachable
}

// paymentMiscFee is models.DecimalField of a plan fee as both callers read it:
// to_python's value, the INSERT parameter and what auditlog records of the
// instance.
type paymentMiscFee struct {
	// Value is str() of to_python's value: the parameter the lookup and the
	// INSERT send (psycopg adapts a Decimal by its text; Django 5 no longer
	// quantizes it client side). "" for None, which the repository reads as
	// amount IS NULL.
	Value string
	// Audit is auditlog's smart_str of the value as assigned to the instance
	// (str(value): a string keeps its spelling, a float its repr).
	Audit string
	// Float is the assigned value when it is a Python float.
	Float *float64
	// Null is a null fee: the lookup matches NULL and the INSERT violates NOT NULL.
	Null bool
	// OutOfRange is a value the numeric(9, 2) column refuses: PostgreSQL rounds
	// it half away from zero to an absolute value of 10^7 or more and raises
	// DataError at the statement, after every earlier create.
	OutOfRange bool
	// PostgresOverflow is a value PostgreSQL's numeric cannot represent at all
	// (a stored exponent beyond its digit limits): the amount lookup's own
	// parameter raises DataError, so the exception ends the run before that
	// quote is read.
	PostgresOverflow bool
}

// paymentMiscFeeValue is models.DecimalField.to_python: Decimal() of a string
// or integer, Context(prec=9).create_decimal_from_float of a float. The
// exceptions to_python raises (a non-finite value, an invalid literal, a list
// or an object) are errPaymentMiscStop: they end the run where Django's try
// does, before that quote's INSERT.
func paymentMiscFeeValue(fee any) (paymentMiscFee, error) {
	switch v := fee.(type) {
	case nil:
		// to_python(None) is None: the lookup matches NULL and the insert fails.
		return paymentMiscFee{Null: true, Audit: "None"}, nil
	case bool:
		// Decimal(True) is Decimal(1).
		if v {
			return paymentMiscFee{Value: "1", Audit: "True"}, nil
		}
		return paymentMiscFee{Value: "0", Audit: "False"}, nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, err := strconv.ParseFloat(string(v), 64)
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return paymentMiscFee{}, errPaymentMiscStop
			}
			if math.IsInf(number, 0) || math.IsNaN(number) {
				// create_decimal_from_float gives a non-finite value: to_python's ValidationError.
				return paymentMiscFee{}, errPaymentMiscStop
			}
			return paymentMiscFeeText(dto.DecimalFromFloat9(number), dto.PythonFloatStr(number), &number)
		}
		// str(int) is its digits ("-0" is 0).
		integer, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return paymentMiscFee{}, errPaymentMiscStop
		}
		return paymentMiscFeeText(integer.String(), integer.String(), nil)
	case string:
		return paymentMiscFeeText(v, v, nil)
	}
	// decimal.Decimal(list) is a malformed tuple and Decimal(dict) a TypeError.
	return paymentMiscFee{}, errPaymentMiscStop
}

// paymentMiscFeeText converts Decimal's str() of the assigned value (structured
// by dto.ParsePythonDecimal, or dto.DecimalFromFloat9's nine digits) and the
// audit text.
func paymentMiscFeeText(decimal, audit string, float *float64) (paymentMiscFee, error) {
	if paymentMiscDecimalExponentRefused(decimal) {
		// Decimal() is InvalidOperation past MAX_EMAX; a value this far outside
		// the column is outside PostgreSQL's numeric too, where Django's lookup
		// raises DataError and the run stops the same way. The bound also keeps
		// str() of the value from being written out.
		return paymentMiscFee{}, errPaymentMiscStop
	}
	value := dto.ParsePythonDecimal(decimal)
	if value.Kind != "finite" {
		// "" is InvalidOperation; "inf" and "nan" are the non-finite values
		// to_python refuses (ValidationError).
		return paymentMiscFee{}, errPaymentMiscStop
	}
	return paymentMiscFee{Value: value.Text, Audit: audit, Float: float, OutOfRange: paymentMiscFeeOutOfRange(value.Text),
		PostgresOverflow: paymentMiscFeePostgresOverflow(value.Text)}, nil
}

// paymentMiscFeePostgresOverflow reports a value PostgreSQL's numeric cannot
// represent: at most 131072 digits before the decimal point and a scale of
// 16383 ("value overflows numeric format"; measured on PostgreSQL 16:
// '1e131071' and '1e-16383' are accepted, '1e131072' and '1e-16384' are not).
func paymentMiscFeePostgresOverflow(text string) bool {
	digits, exponent, ok := paymentMiscNumeral(text)
	if !ok || digits == "0" {
		return false
	}
	return exponent+int64(len(digits))-1 > 131071 || exponent < -16383
}

// paymentMiscNumeral splits Decimal's str() into its significant digits (no
// leading zeros, "0" for zero) and the exponent of the last digit.
func paymentMiscNumeral(text string) (digits string, exponent int64, ok bool) {
	unsigned := strings.TrimLeft(text, "+-")
	if index := strings.IndexAny(unsigned, "eE"); index >= 0 {
		parsed, err := strconv.ParseInt(unsigned[index+1:], 10, 64)
		if err != nil {
			return "", 0, false
		}
		exponent, unsigned = parsed, unsigned[:index]
	}
	whole, fraction, _ := strings.Cut(unsigned, ".")
	digits = strings.TrimLeft(whole+fraction, "0")
	if digits == "" {
		return "0", 0, true
	}
	return digits, exponent - int64(len(fraction)), true
}

// paymentMiscFeeOutOfRange reports a value the numeric(9, 2) column refuses:
// PostgreSQL rounds it half away from zero and raises when the absolute value
// reaches 10^7, so 9999999.995 overflows and 9999999.9949 does not. The
// comparison is exact.
func paymentMiscFeeOutOfRange(text string) bool {
	digits, exponent, ok := paymentMiscNumeral(text)
	if !ok || digits == "0" {
		return false
	}
	if top := exponent + int64(len(digits)) - 1; top != 6 {
		return top > 6
	}
	// |value| = digits * 10^exponent and 9999999.995 = 9999999995 * 10^-3;
	// top == 6 fixes exponent = 7 - len(digits), so the scaling stays small.
	scaled, threshold := new(big.Int), new(big.Int).SetInt64(9999999995)
	scaled.SetString(digits, 10)
	if shift := exponent + 3; shift >= 0 {
		scaled.Mul(scaled, paymentMiscPow10(shift))
	} else {
		threshold.Mul(threshold, paymentMiscPow10(-shift))
	}
	return scaled.Cmp(threshold) >= 0
}

// paymentMiscPow10 is 10^n as an integer.
func paymentMiscPow10(n int64) *big.Int {
	if n <= 0 {
		return big.NewInt(1)
	}
	return new(big.Int).Exp(big.NewInt(10), big.NewInt(n), nil)
}

// paymentMiscDecimalExponent is the exponent a Decimal literal writes ("" when
// it has none), without its sign, underscores or leading zeros.
func paymentMiscDecimalExponent(text string) string {
	index := strings.LastIndexAny(text, "eE")
	if index < 0 {
		return ""
	}
	exponent := strings.TrimLeft(strings.ReplaceAll(text[index+1:], "_", ""), "+-")
	return strings.TrimLeft(exponent, "0")
}

// paymentMiscDecimalExponentRefused reports a Decimal text whose exponent this
// port refuses before building the value: beyond decimal.MAX_EMAX (Python's
// InvalidOperation) or far outside PostgreSQL's numeric (where Django's lookup
// raises DataError and nothing is listed either).
func paymentMiscDecimalExponentRefused(text string) bool {
	const limit = "999999999999999999"
	exponent := paymentMiscDecimalExponent(text)
	switch {
	case exponent == "":
		return false
	case len(exponent) > len(limit):
		return true
	case len(exponent) == len(limit):
		return exponent > limit
	}
	value, err := strconv.ParseInt(exponent, 10, 64)
	return err == nil && value > 1000000
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
		parsed, err := paymentStateDateAny(value)
		if err != nil {
			return run, errPaymentMiscStop
		}
		day = *parsed
	}
	if run.from, run.to, err = paymentMiscBalanceYear(now, run.association.StartDay, run.association.StartMonth); err != nil {
		return run, err
	}
	run.plans, err = paymentMiscActivePlans(run.association.Plans, day.Day(), int(day.Month()))
	if err != nil && !errors.Is(err, errPaymentMiscStop) {
		return fail(err)
	}
	return run, err
}

// quotes visits every plan's subscriptions without an existing quote, in
// the views' order; visit returning errPaymentMiscStop ends the loop. loadEdge
// is the route's measured json.loads edge for the subscriptions' payment rows
// (0: not measured, no check): Django's queryset loads them with
// select_related, so a deeper stored value raises before the plan's first write
// and the task's except Exception ends the run there.
func (r *paymentMiscRun) quotes(ctx context.Context, loadEdge int, visit func(paymentQuotePlan, domain.PaymentSimulationSubscription, paymentMiscFee) error) error {
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
		if loadEdge > 0 {
			for _, subscription := range subscriptions {
				if paymentMiscSubscriptionPaymentDepth(subscription) > loadEdge {
					return errPaymentMiscStop
				}
			}
		}
		for _, subscription := range subscriptions {
			idPattern, err := paymentMiscPattern(plan.id)
			if err != nil {
				return err
			}
			fee, err := paymentMiscFeeValue(plan.fee)
			if err != nil {
				return err
			}
			if fee.PostgresOverflow {
				// The lookup's own numeric parameter raises: the exception ends
				// the run before this quote is read.
				return errPaymentMiscStop
			}
			// A value numeric(9, 2) cannot hold matches no stored amount; the
			// quote is still listed and the apply's INSERT raises.
			exists, err := r.tx.QuoteExists(ctx, domain.PaymentQuoteCheck{User: subscription.UserID, Associate: subscription.AssociateID,
				Pattern: idPattern, Amount: fee.Value, Group: r.group})
			if err != nil {
				return err
			}
			if exists {
				continue
			}
			if err := visit(plan, subscription, fee); err != nil {
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

// payment_simulation_partial_quotes_apply's measured stored-JSON recursion
// edges (owner decision X15), measured with CPython 3.13.16 on the parity
// harness's Django, for the payment rows the task loads and creates:
//   - the subscriptions' payments are loaded with select_related (loads 9928):
//     a deeper value raises before the plan's first write and the task's except
//     Exception answers the view's 200 body;
//   - each created payment's INSERT runs psycopg2's json.dumps (adapt~9921),
//     deeper than which nothing of that quote is written;
//   - the create entry's index resolver reloads the new row (loads 9912,
//     caught): past it the index falls back.
//
// The created payments' meta is generated by the task, so only the loaded rows
// can carry a deeper value; the create edges are set for the save hook anyway.
const (
	paymentMiscApplyLoadJSONDepth   = 9928
	paymentMiscApplyInsertJSONDepth = 9921
	paymentMiscApplyIndexJSONDepth  = 9912
)

// paymentMiscSubscriptionPaymentDepth is the deepest nesting of the
// subscription's payment JSON columns (0 when all are NULL).
func paymentMiscSubscriptionPaymentDepth(subscription domain.PaymentSimulationSubscription) int {
	return paymentJSONTextsDepth(subscription.PaymentMetaPaymentCategories, subscription.PaymentMeta, subscription.PaymentCourse)
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
	err = run.quotes(ctx, 0, func(plan paymentQuotePlan, subscription domain.PaymentSimulationSubscription, _ paymentMiscFee) error {
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

// paymentMiscMail is one send_email_html call of the apply, published after the commit.
type paymentMiscMail struct {
	subject, message string
	// recipient is the user's e-mail (bakney_user.email is NOT NULL).
	recipient string
	// start runs the configured send_email after the Timer delay; nil publishes send_mail_async.
	start func()
}

// Apply follows payment_simulation_partial_quotes_apply. Every quote is a
// Payment.objects.create with its audited create entry; Django commits each
// one, so a Python exception keeps the earlier ones. A quote for a user other
// than the association owner renders email/payment/fee_assigned.html and calls
// send_email_html: without a CommunicationConfiguration it publishes
// send_mail_async (after the commit, in order; its own try/except swallows
// failures). With a configuration of the association send_email_html loads
// it and sends on a Timer thread after random.randint(1, 30) seconds: Go
// plans each send before the commit and starts its timer after it.
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
	mails := []paymentMiscMail{}
	configured := false
	err = run.quotes(ctx, paymentMiscApplyLoadJSONDepth, func(plan paymentQuotePlan, subscription domain.PaymentSimulationSubscription,
		fee paymentMiscFee) error {
		// A null fee violates NOT NULL and a fee beyond numeric(9, 2) overflows: the insert raises.
		if fee.Null || fee.OutOfRange {
			return errPaymentMiscStop
		}
		other := subscription.UserID != run.association.OwnerID
		if other && subscription.AssociateID != nil {
			if s.mails == nil {
				return domain.ErrServerComposition
			}
			found, err := tx.CommunicationConfigured(ctx, run.association.ID)
			if err != nil {
				return err
			}
			if found && s.configured == nil {
				// cmd/server always calls WithConfiguredMail.
				return domain.ErrServerComposition
			}
			configured = found
		}
		account, err := tx.DefaultBankAccount(ctx, run.association.ID)
		if err != nil {
			return err
		}
		// The column stores the header's uuid; the instance (auditlog) keeps the text as sent.
		group, sentGroup, err := s.writes.createGroupSent(ctx, tx, request.GroupHeader)
		if errors.Is(err, domain.ErrPaymentWritesServer) {
			return errPaymentMiscStop
		}
		if err != nil {
			return err
		}
		user, association := subscription.UserID, run.association.ID
		m := &paymentMemory{row: domain.PaymentWriteRow{ID: s.writes.newID(), Type: "default", Subject: 1, CreationDate: s.writes.at(),
			UserID: &user, AssociateID: subscription.AssociateID, AssociationID: &association, AccountID: account, GroupID: group},
			jsonAudit: map[string]string{}, groupSent: sentGroup}
		// The column and the instance keep to_python's value; auditlog str() of
		// the assigned one.
		m.row.Amount, m.amountAudit, m.amountShown = fee.Value, &fee.Audit, &fee.Value
		m.amountFloat = fee.Float
		m.setJSON("meta", paymentQuoteMeta(plan), &m.row.Meta)
		// The quote's INSERT is its first JSON write and the create entry's
		// resolver reloads the new row one level later.
		createCtx := withPaymentJSONEdges(ctx, paymentJSONEdges{Load: paymentMiscApplyInsertJSONDepth, Save: paymentMiscApplyInsertJSONDepth,
			Index: paymentMiscApplyIndexJSONDepth})
		createFallback, err := s.writes.defaults(createCtx, tx, m)
		if err != nil {
			return err
		}
		if err := tx.InsertPaymentRow(ctx, m.row); err != nil {
			return err
		}
		created, err := s.writes.values(ctx, tx, m)
		if err != nil {
			return err
		}
		if err := s.writes.audit(ctx, tx, identity, request.Audit, m.row.ID, 0, createdPaymentChanges(created), m.row.AssociationID,
			createFallback); err != nil {
			return err
		}
		if !other {
			return nil
		}
		if subscription.AssociateID == nil {
			// subscription.associate.first_name on None while rendering the e-mail.
			return errPaymentMiscStop
		}
		mail, err := s.feeMail(ctx, run.association, plan, subscription, request.Language)
		if err != nil {
			return err
		}
		if configured {
			association, err := uuid.Parse(run.association.ID)
			if err != nil {
				return err
			}
			mail.start, err = s.configured.PlanEmailHTMLInProcess(ctx, association, mail.message, mail.subject, []any{mail.recipient},
				[]any{optionalValue(run.association.OwnerEmail)})
			if errors.Is(err, celeryworker.ErrHandoff) {
				// The planner hands off a message outside smtpsend's subset; it
				// wraps that refusal, which is what this request answers.
				return err
			}
			if err != nil {
				return err
			}
		}
		mails = append(mails, mail)
		return nil
	})
	if err != nil && !errors.Is(err, errPaymentMiscStop) {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	for _, mail := range mails {
		if mail.start != nil {
			mail.start()
			continue
		}
		// send_email_html's try/except swallows a failed publish.
		_ = s.mails.sendMail(ctx, mail.subject, mail.message, s.mails.settings.DefaultFromEmail, []any{mail.recipient}, run.association.ID,
			[]any{optionalValue(run.association.OwnerEmail)})
	}
	return nil
}

// feeMail renders association_quote_assign's e-mail: the template with the
// plan's stored name and fee, the subject with the denomination. Every JSON
// value renders as Django's str() of it; a renderer refusal is returned
// unchanged (counted under emailrender).
func (s *PaymentMisc) feeMail(ctx context.Context, association *domain.PaymentSimulationAssociation, plan paymentQuotePlan,
	subscription domain.PaymentSimulationSubscription, language string) (paymentMiscMail, error) {
	name, err := quoteAssignTemplateValue(plan.name)
	if err != nil {
		return paymentMiscMail{}, err
	}
	amount, err := quoteAssignTemplateValue(plan.fee)
	if err != nil {
		return paymentMiscMail{}, err
	}
	if subscription.UserEmail == nil {
		// bakney_user.email is NOT NULL (AbstractUser's EmailField) and the
		// subscription query joins the user: no row yields a null e-mail, so
		// recipient_list is never [None].
		return paymentMiscMail{}, domain.ErrPaymentMiscUnreachable
	}
	message, err := s.mails.render(ctx, emailrender.FeeAssigned, map[string]any{
		"recipient_name": paymentMiscText(subscription.AssociateFirst) + " " + paymentMiscText(subscription.AssociateLast),
		"fee_name":       name, "amount": amount, "application_name": s.whitelabel, "association_name": optionalValue(association.Denomination)},
		language)
	if err != nil {
		return paymentMiscMail{}, err
	}
	subject := paymentMiscText(association.Denomination) + " | Nuova quota associativa '" + dto.PythonStr(plan.name) + "' assegnata"
	return paymentMiscMail{subject: subject, message: message, recipient: *subscription.UserEmail}, nil
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

// paymentSignJSONEdges are payment_sign's measured edges: the view's own load,
// auditlog's refetch of the stored row before the payment UPDATE, and the audit
// index resolver's reload after the log entry.
var paymentSignJSONEdges = paymentJSONEdges{Load: 9936, Save: 9934, Index: 9926}

// Sign follows payment_sign. Storing a signature creates the Signature (the
// scope user, or the selected person for collaborators), sets it on the
// payment and runs Payment.save(), commits, then print_document_compensation.apply.
func (s *PaymentMisc) Sign(ctx context.Context, identity domain.ResolvedIdentity, request PaymentMiscRequest) (map[string]any, error) {
	ctx = withPaymentJSONEdges(ctx, paymentSignJSONEdges)
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
	found, paymentAssociation, err := tx.SignPayment(ctx, key, supplierGroup(request.GroupHeader), paymentStateScope(identity))
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, &dto.MessageReadUUIDError{Detail: "payment not found"}
	}
	// The view's own load of the payment row: its JSON columns are decoded
	// before the association check and the signature payload.
	row, err := tx.PaymentState(ctx, domain.PaymentStateQuery{ID: key, Scope: paymentStateScope(identity),
		Group: supplierGroup(request.GroupHeader)})
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrPaymentMiscServer
	}
	if err = paymentJSONLoadFailure(ctx, &row.PaymentSaveRow); err != nil {
		return nil, err
	}
	// Payment signing only applies to the caller's association (request.user.sport_association).
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, nil)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, domain.ErrPaymentMiscServer
	}
	if err != nil {
		return nil, err
	}
	if paymentAssociation == nil || *paymentAssociation != association {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
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
		if s.printer == nil {
			return nil, domain.ErrServerComposition
		}
		if err := s.storeSignature(ctx, tx, identity, request, row, data); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		if err := s.printer.Compensation(ctx, key, request.Authorization); err != nil {
			return nil, err
		}
	}
	return map[string]any{"status": "success"}, nil
}

// storeSignature is Signature.objects.create, payment.signature = it and
// payment.save(): Payment.save() defaults plus the audited update. The
// signature rows are written before the payment save, as the view does: a
// stored value past the save's edge leaves them committed (the view is not in
// a transaction) and the payment untouched.
func (s *PaymentMisc) storeSignature(ctx context.Context, tx domain.PaymentMiscTransaction, identity domain.ResolvedIdentity,
	request PaymentMiscRequest, row *domain.PaymentStateRow, data string) error {
	user := identity.ScopeUser
	if identity.Collaborator && identity.Effective != nil {
		user = identity.Effective
	}
	audit := paymentCategoryAuditContext(identity, request.Audit)
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	signature := s.writes.newID()
	at := s.writes.at()
	if err := tx.CreatePaymentSignature(ctx, domain.PaymentSignatureSave{ID: signature, User: user.ID, Data: data, Audit: audit,
		AssociationID: actorAssociation, At: at}); err != nil {
		return err
	}
	changes := map[string][2]string{}
	if err = paymentSaveDefaults(ctx, tx, &row.PaymentSaveRow, changes); err != nil {
		// Payment.save() raising after the Signature.objects.create writes: the
		// request is not atomic, so they stay.
		return paymentJSONCommitAfterWrites(tx, err)
	}
	previous, err := tx.SetPaymentSignature(ctx, row.ID, signature)
	if err != nil {
		return err
	}
	before := "None"
	if previous != nil {
		before = *previous
	}
	changes["signature"] = [2]string{before, signature}
	if err := tx.SavePaymentState(ctx, domain.PaymentStateSave{ID: row.ID, Paid: row.Paid, PaymentDate: row.PaymentDate,
		CategoryID: row.CategoryID, Description: row.Description}); err != nil {
		return err
	}
	return tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		IndexFallback: row.IndexFallback, Context: audit, At: at})
}
