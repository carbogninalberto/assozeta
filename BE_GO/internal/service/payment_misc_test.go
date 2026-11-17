package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
)

// The Python expectations in these tests are the measured values of
// int()/Decimal()/format_number()/str() and Django's template rendering
// (BE/application/utils/payments_utils.py, BE/application/tasks.py,
// BE/application/models/payment_models.py).

func TestPaymentMiscIntReadsEveryJSONInteger(t *testing.T) {
	for _, test := range []struct {
		name  string
		value any
		want  string
		stops bool
	}{
		{name: "30-digit bound", value: json.Number("123456789012345678901234567890"), want: "123456789012345678901234567890"},
		{name: "negative", value: json.Number("-00042"), want: "-42"},
		{name: "float truncates", value: json.Number("15.9"), want: "15"},
		{name: "float exponent", value: json.Number("1E2"), want: "100"},
		{name: "double beyond int64", value: json.Number("1e19"), want: "10000000000000000000"},
		{name: "true", value: true, want: "1"},
		{name: "false", value: false, want: "0"},
		{name: "underscores", value: "1_000", want: "1000"},
		{name: "other script digits", value: "\uff11", want: "1"},
		{name: "other script with spaces", value: " \u0668 ", want: "8"},
		{name: "two digit script", value: "\u0663\u0661", want: "31"},
		{name: "plus sign", value: "+7", want: "7"},
		{name: "text", value: "abc", stops: true},
		{name: "decimal text", value: "1.5", stops: true},
		{name: "double underscore", value: "1__0", stops: true},
		{name: "infinity", value: json.Number("1e400"), stops: true},
		{name: "null", value: nil, stops: true},
		{name: "list", value: []any{1}, stops: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := paymentMiscInt(test.value)
			if test.stops {
				if !errors.Is(err, errPaymentMiscStop) {
					t.Fatalf("want a Python exception, got %v (%v)", got, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got.String() != test.want {
				t.Fatalf("want %s, got %s", test.want, got.String())
			}
		})
	}
}

func TestPaymentMiscActivePlansSkipsA30DigitDayBound(t *testing.T) {
	plans := `[{"id": "plan", "name": "Quota", "subscription_fee": "10", "auto_assign": true, "advanced_options": true,
		"previous_subscription_fee_plan": "prev", "from_day": 123456789012345678901234567890, "from_month": 9,
		"to_day": 30, "to_month": 9}]`
	active, err := paymentMiscActivePlans(&plans, 1, 9)
	if err != nil {
		t.Fatal(err)
	}
	if len(active) != 0 {
		t.Fatalf("a 30-digit from_day equals no date: %v", active)
	}
}

func TestPaymentMiscActivePlansRendersEveryIntInFull(t *testing.T) {
	plans := `[{"id": "plan", "name": "Quota", "subscription_fee": "10", "auto_assign": true, "advanced_options": true,
		"previous_subscription_fee_plan": "prev", "from_day": 1, "from_month": 9,
		"to_day": 123456789012345678901234567890, "to_month": 9}]`
	active, err := paymentMiscActivePlans(&plans, 1, 9)
	if err != nil {
		t.Fatal(err)
	}
	if len(active) != 1 {
		t.Fatalf("one active plan, got %d", len(active))
	}
	rendered := active[0].render()
	got, _ := rendered.Lookup("to_day")
	if want := json.Number("123456789012345678901234567890"); got != want {
		t.Fatalf("to_day: want %s, got %#v", want, got)
	}
}

func TestPaymentMiscPatternIsPythonStrOfEveryValue(t *testing.T) {
	for _, test := range []struct {
		name  string
		value any
		want  string
		stops bool
	}{
		{name: "string", value: "plan-a", want: "%plan-a%"},
		{name: "underscores escaped", value: "a_b", want: `%a\_b%`},
		{name: "percent escaped", value: "5%", want: `%5\%%`},
		{name: "backslash escaped", value: `a\b`, want: `%a\\b%`},
		{name: "number", value: json.Number("4242"), want: "%4242%"},
		{name: "float number", value: json.Number("4.5"), want: "%4.5%"},
		{name: "true", value: true, want: "%True%"},
		{name: "list", value: []any{"p1"}, want: "%['p1']%"},
		{name: "object", value: domain.RequestObject{{Name: "k", Value: "v"}}, want: "%{'k': 'v'}%"},
		{name: "null", value: nil, stops: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := paymentMiscPattern(test.value)
			if test.stops {
				if !errors.Is(err, errPaymentMiscStop) {
					t.Fatalf("Cannot use None as a query value ends the run, got %v (%q)", err, got)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got != test.want {
				t.Fatalf("want %q, got %q", test.want, got)
			}
		})
	}
	if _, err := paymentMiscPattern(struct{}{}); !errors.Is(err, domain.ErrPaymentMiscUnreachable) {
		t.Fatalf("a value json.loads cannot produce is an unreachable state: %v", err)
	}
}

func TestPaymentMiscFeeMatchesDecimalField(t *testing.T) {
	for _, test := range []struct {
		name  string
		fee   any
		value string
		audit string
		float bool
		null  bool
		out   bool
		stops bool
	}{
		{name: "underscored text", fee: "1_000", value: "1000", audit: "1_000"},
		{name: "other script digits", fee: "\u0661\u0662.\u0665\u0660", value: "12.50", audit: "\u0661\u0662.\u0665\u0660"},
		{name: "spaces kept in the audit", fee: " 12.5 ", value: "12.5", audit: " 12.5 "},
		{name: "exponent text", fee: "1e2", value: "1E+2", audit: "1e2"},
		{name: "tiny exponent", fee: "1e-999", value: "1E-999", audit: "1e-999"},
		{name: "rounding is PostgreSQL's", fee: "0.005", value: "0.005", audit: "0.005"},
		{name: "three decimals", fee: "2.665", value: "2.665", audit: "2.665"},
		{name: "negative three decimals", fee: "-0.005", value: "-0.005", audit: "-0.005"},
		{name: "just under the range", fee: "9999999.9949", value: "9999999.9949", audit: "9999999.9949"},
		{name: "at the range edge", fee: "9999999.995", value: "9999999.995", audit: "9999999.995", out: true},
		{name: "past the range", fee: "10000000", value: "10000000", audit: "10000000", out: true},
		{name: "negative past the range", fee: "-10000000", value: "-10000000", audit: "-10000000", out: true},
		{name: "huge exponent text", fee: "1e999", value: "1E+999", audit: "1e999", out: true},
		{name: "integer", fee: json.Number("3"), value: "3", audit: "3"},
		{name: "30-digit integer", fee: json.Number("123456789012345678901234567890"),
			value: "123456789012345678901234567890", audit: "123456789012345678901234567890", out: true},
		{name: "negative zero integer", fee: json.Number("-0"), value: "0", audit: "0"},
		{name: "float keeps nine digits", fee: json.Number("12.5"), value: "12.5000000", audit: "12.5", float: true},
		{name: "float three decimals", fee: json.Number("0.125"), value: "0.125000000", audit: "0.125", float: true},
		{name: "float past the range", fee: json.Number("1e7"), value: "10000000.0", audit: "10000000.0", out: true, float: true},
		{name: "float beyond the double range", fee: json.Number("1e400"), stops: true},
		{name: "null", fee: nil, null: true, audit: "None"},
		{name: "true", fee: true, value: "1", audit: "True"},
		{name: "false", fee: false, value: "0", audit: "False"},
		{name: "infinity text", fee: "Infinity", stops: true},
		{name: "nan text", fee: "snan", stops: true},
		{name: "other decimal separator", fee: "12\u066b50", stops: true},
		{name: "text", fee: "abc", stops: true},
		{name: "list", fee: []any{1}, stops: true},
		{name: "object", fee: domain.RequestObject{{Name: "a", Value: 1}}, stops: true},
		{name: "exponent past MAX_EMAX", fee: "1e1000000000000000000", stops: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := paymentMiscFeeValue(test.fee)
			if test.stops {
				if !errors.Is(err, errPaymentMiscStop) {
					t.Fatalf("want the exception to_python raises, got %+v (%v)", got, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got.Value != test.value || got.Audit != test.audit || got.Null != test.null ||
				got.OutOfRange != test.out || (got.Float != nil) != test.float {
				t.Fatalf("want value=%q audit=%q float=%v null=%v out=%v, got %+v",
					test.value, test.audit, test.float, test.null, test.out, got)
			}
		})
	}
}

func TestPaymentMiscFeeNumericParameterFollowsPostgreSQL(t *testing.T) {
	// Measured on PostgreSQL 16: '1e131071' and '1e-16383' are numeric values,
	// '1e131072' and '1e-16384' raise "value overflows numeric format".
	for _, test := range []struct {
		text string
		want bool
	}{
		{"1E+131071", false}, {"1E+131072", true}, {"1E-16383", false}, {"1E-16384", true},
		{"1E+999", false}, {"12.5", false}, {"0", false}, {"1E-999", false},
		{"123456789012345678901234567890", false},
	} {
		if got := paymentMiscFeePostgresOverflow(test.text); got != test.want {
			t.Errorf("%s: want over PostgreSQL's numeric %v, got %v", test.text, test.want, got)
		}
	}
}

func TestPaymentMiscFeeOutOfRangeFollowsPostgreSQL(t *testing.T) {
	// Measured on PostgreSQL 16: '0.005'::numeric(9,2) is 0.01 (half away from
	// zero), '9999999.995' overflows, '9999999.9949' does not.
	for _, test := range []struct {
		text string
		want bool
	}{
		{"0.005", false}, {"2.665", false}, {"9999999.9949", false}, {"-9999999.9949", false},
		{"9999999.995", true}, {"-9999999.995", true}, {"10000000", true}, {"-10000000", true},
		{"1E+999", true}, {"1E-999", false}, {"0", false}, {"-0", false}, {"0.00", false},
		{"123456789012345678901234567890", true}, {"1.5E+6", false}, {"999999.9999", false},
	} {
		if got := paymentMiscFeeOutOfRange(test.text); got != test.want {
			t.Errorf("%s: want out-of-range %v, got %v", test.text, test.want, got)
		}
	}
}

func TestPaymentMiscFeeNullMatchesIsNull(t *testing.T) {
	// The repository reads the empty lookup as amount IS NULL.
	got, err := paymentMiscFeeValue(nil)
	if err != nil || got.Value != "" || !got.Null {
		t.Fatalf("null fee: %+v (%v)", got, err)
	}
}

func TestQuoteAssignTemplateValuePrintsLikeDjango(t *testing.T) {
	for _, test := range []struct {
		name   string
		value  any
		want   any
		safe   bool
		inf    bool
		broken bool
	}{
		{name: "none", value: nil, want: nil},
		{name: "string", value: "Quota", want: "Quota"},
		{name: "bool", value: true, want: true},
		{name: "integer", value: json.Number("15"), want: int64(15)},
		{name: "negative zero integer", value: json.Number("-0"), want: int64(0)},
		{name: "30-digit integer", value: json.Number("123456789012345678901234567890"),
			want: emailrender.Safe("123456789012345678901234567890"), safe: true},
		{name: "float", value: json.Number("12.5"), want: 12.5},
		{name: "float beyond the double range", value: json.Number("1e400"), inf: true},
		{name: "list", value: []any{"a", json.Number("1")}, want: emailrender.Safe("[&#x27;a&#x27;, 1]"), safe: true},
		{name: "object", value: domain.RequestObject{{Name: "a", Value: nil}},
			want: emailrender.Safe("{&#x27;a&#x27;: None}"), safe: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := quoteAssignTemplateValue(test.value)
			if test.broken {
				if !errors.Is(err, domain.ErrPaymentMiscUnreachable) {
					t.Fatalf("want an unreachable state, got %#v (%v)", got, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if test.inf {
				if number, ok := got.(float64); !ok || !math.IsInf(number, 1) {
					t.Fatalf("a huge literal is the infinity json.loads reads: %#v", got)
				}
				return
			}
			if got != test.want {
				t.Fatalf("want %#v, got %#v", test.want, got)
			}
		})
	}
	if _, err := quoteAssignTemplateValue(struct{}{}); !errors.Is(err, domain.ErrPaymentMiscUnreachable) {
		t.Fatalf("a value json.loads cannot produce is an unreachable state: %v", err)
	}
}

func TestPaymentMiscFeeMailRendersEveryValueKind(t *testing.T) {
	plan := paymentQuotePlan{name: []any{"Quota", json.Number("1")}, fee: json.Number("4.5")}
	name, err := quoteAssignTemplateValue(plan.name)
	if err != nil {
		t.Fatal(err)
	}
	amount, err := quoteAssignTemplateValue(plan.fee)
	if err != nil {
		t.Fatal(err)
	}
	brand, err := emailrender.Brand(nil, emailrender.Settings{AppURL: "https://app.example.test", WhitelabelName: "Assozeta"})
	if err != nil {
		t.Fatal(err)
	}
	html, err := emailrender.Render(emailrender.FeeAssigned, map[string]any{
		"recipient_name": "Mario Rossi", "fee_name": name, "amount": amount, "application_name": "Assozeta",
		"association_name": "Società"}, emailrender.Options{Language: "en", Brand: brand})
	if err != nil {
		t.Fatal(err)
	}
	for _, fragment := range []string{"&#x27;Quota&#x27;, 1", "con importo 4.5 €"} {
		if !strings.Contains(html, fragment) {
			t.Fatalf("the fee e-mail renders %q:\n%s", fragment, html)
		}
	}
}

// paymentMiscApplyStore is the transaction payment_simulation_partial_quotes_apply
// opens: the association's plans, one subscription and the quote's writes.
type paymentMiscApplyStore struct {
	domain.PaymentMiscStore
	tx *paymentMiscApplyTx
}

func (s *paymentMiscApplyStore) BeginPaymentMisc(context.Context) (domain.PaymentMiscTransaction, error) {
	return s.tx, nil
}

type paymentMiscApplyTx struct {
	domain.PaymentMiscTransaction
	association   *domain.PaymentSimulationAssociation
	subscriptions []domain.PaymentSimulationSubscription
	existing      bool
	rows          []domain.PaymentWriteRow
	audits        []domain.PaymentWritesAuditEntry
	commits       int
	rollbacks     int
}

func (t *paymentMiscApplyTx) ScopedAssociation(context.Context, string, *string) (string, error) {
	return t.association.ID, nil
}

func (t *paymentMiscApplyTx) SimulationAssociation(context.Context, string) (*domain.PaymentSimulationAssociation, error) {
	return t.association, nil
}

func (t *paymentMiscApplyTx) SimulationSubscriptions(context.Context, domain.PaymentSimulationQuery) (
	[]domain.PaymentSimulationSubscription, error) {
	return t.subscriptions, nil
}

func (t *paymentMiscApplyTx) QuoteExists(context.Context, domain.PaymentQuoteCheck) (bool, error) {
	return t.existing, nil
}

func (t *paymentMiscApplyTx) DefaultBankAccount(context.Context, string) (*string, error) {
	return nil, nil
}

func (t *paymentMiscApplyTx) DefaultPaymentCategory(context.Context, *string) (*string, *string, error) {
	return nil, nil, nil
}

func (t *paymentMiscApplyTx) InsertPaymentRow(_ context.Context, row domain.PaymentWriteRow) error {
	t.rows = append(t.rows, row)
	return nil
}

func (t *paymentMiscApplyTx) PaymentAuditValues(context.Context, string) (map[string]string, error) {
	values := map[string]string{"type": "default", "subject": "1", "amount": "10.00", "sport_association": t.association.ID,
		"meta": "null"}
	if len(t.rows) > 0 && t.rows[len(t.rows)-1].Meta != nil {
		values["meta"] = *t.rows[len(t.rows)-1].Meta
	}
	return values, nil
}

func (t *paymentMiscApplyTx) AppendPaymentWritesAudit(_ context.Context, entry domain.PaymentWritesAuditEntry) error {
	t.audits = append(t.audits, entry)
	return nil
}

func (t *paymentMiscApplyTx) Commit() error {
	t.commits++
	return nil
}

func (t *paymentMiscApplyTx) Rollback() error {
	t.rollbacks++
	return nil
}

// paymentMiscApplyEdgeDay is the plan's start day and month at the test's now.
func paymentMiscApplyFixture(depth int) *paymentMiscApplyTx {
	now := time.Date(2026, 11, 10, 19, 45, 3, 0, time.UTC)
	plans := `[{"id": "plan-a", "name": "Quota", "subscription_fee": "10", "auto_assign": true, "advanced_options": true,
		"previous_subscription_fee_plan": "plan-0", "from_day": ` + strconv.Itoa(now.Day()) + `, "from_month": ` + strconv.Itoa(int(now.Month())) + `,
		"to_day": 30, "to_month": 9}]`
	owner, email := "owner", "owner@example.test"
	course := jsonEdgesArray(depth)
	return &paymentMiscApplyTx{
		association: &domain.PaymentSimulationAssociation{ID: "a1000000-0000-4000-8000-000000988880", OwnerID: owner,
			Plans: &plans, StartDay: 1, StartMonth: int(time.January), OwnerEmail: &email},
		subscriptions: []domain.PaymentSimulationSubscription{{ID: "a1000000-0000-4000-8000-000000988881", UserID: owner,
			AssociateID: &email, PaymentCourse: course}},
	}
}

// payment_simulation_partial_quotes_apply's measured JSON edges (owner decision
// X15): Django loads the subscriptions' payments with select_related (loads
// 9928), so a deeper stored value raises before the plan's first write and the
// task's except Exception answers the view's 200 body; a value it parses leaves
// the quote's own INSERT (adapt~9921) to the create edges.
func TestPaymentMiscApplyStoredPaymentJSONDepth(t *testing.T) {
	for _, test := range []struct {
		name  string
		depth int
		quote bool
	}{
		{name: "below the load edge", depth: paymentMiscApplyLoadJSONDepth - 1, quote: true},
		{name: "at the load edge", depth: paymentMiscApplyLoadJSONDepth, quote: true},
		{name: "past the load edge", depth: paymentMiscApplyLoadJSONDepth + 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			tx := paymentMiscApplyFixture(test.depth)
			writes := NewPaymentMisc(&paymentMiscApplyStore{tx: tx}, func() time.Time {
				return time.Date(2026, 11, 10, 19, 45, 3, 0, time.UTC)
			}, func() string { return "a1000000-0000-4000-8000-000000988882" }, "Bakney")
			identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: tx.association.OwnerID, Role: domain.RoleAssociation},
				Actor: &domain.Account{ID: tx.association.OwnerID}}
			if err := writes.Apply(context.Background(), identity, PaymentMiscRequest{}); err != nil {
				t.Fatalf("err=%v", err)
			}
			if !test.quote {
				// The load ended the run before this plan's first write.
				if len(tx.rows) != 0 || tx.audits != nil || tx.commits != 1 || tx.rollbacks != 1 {
					t.Fatalf("rows=%d audits=%d commits=%d rollbacks=%d", len(tx.rows), len(tx.audits), tx.commits, tx.rollbacks)
				}
				return
			}
			if len(tx.rows) != 1 || len(tx.audits) != 1 || tx.commits != 1 || tx.rollbacks != 1 {
				t.Fatalf("rows=%d audits=%d commits=%d rollbacks=%d", len(tx.rows), len(tx.audits), tx.commits, tx.rollbacks)
			}
			if tx.audits[0].IndexFallback {
				// The quote's own meta is the task's, far below the resolver's edge.
				t.Fatalf("the created entry fell back: %+v", tx.audits[0])
			}
		})
	}
}
