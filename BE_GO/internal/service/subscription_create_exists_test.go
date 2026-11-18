package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"os"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jackc/pgx/v5/pgconn"
)

// subscriptionExistsGolden is one check_if_subscription_exists trace: the raw
// initial_data values as JSON texts, the filters, the statement Django sent and
// the exception str(e) the server (or psycopg2) answered with.
type subscriptionExistsGolden struct {
	First       string  `json:"first"`
	Last        string  `json:"last"`
	Tax         string  `json:"tax"`
	Group       *string `json:"group"`
	Custom      *string `json:"custom"`
	Association string  `json:"association"`
	Start       string  `json:"start"`
	End         string  `json:"end"`
	SQL         *string `json:"sql"`
	Error       *string `json:"error"`
}

func subscriptionExistsDay(t *testing.T, text string) domain.CalendarDate {
	t.Helper()
	parsed, err := time.Parse("2006-01-02", text)
	if err != nil {
		t.Fatalf("date %q: %v", text, err)
	}
	return domain.CalendarDate{Year: parsed.Year(), Month: int(parsed.Month()), Day: parsed.Day()}
}

// TestSubscriptionExistsStatementMatchesDjango checks the statement text
// psycopg2 sends against the ones Django logged (testdata/subscription_exists_statements.json).
func TestSubscriptionExistsStatementMatchesDjango(t *testing.T) {
	raw, err := os.ReadFile("testdata/subscription_exists_statements.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []subscriptionExistsGolden
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("no golden statements")
	}
	for index, test := range cases {
		names := make([]any, 3)
		for i, text := range []string{test.First, test.Last, test.Tax} {
			value, err := dto.ParsePythonJSON(text)
			if err != nil {
				t.Fatalf("case %d: %v", index, err)
			}
			names[i] = value
		}
		statement, err := subscriptionExistsStatement(test.Association, names,
			subscriptionExistsDay(t, test.Start), subscriptionExistsDay(t, test.End), test.Custom, test.Group)
		if test.SQL == nil {
			// A value psycopg2 cannot adapt: nothing is sent.
			var adapt *dto.Psycopg2AdaptError
			if !errors.As(err, &adapt) {
				t.Fatalf("case %d: got (%q, %v), want an adapt error", index, statement, err)
			}
			continue
		}
		if err != nil {
			t.Fatalf("case %d: %v", index, err)
		}
		if statement != *test.SQL {
			t.Fatalf("case %d:\n got: %s\nwant: %s", index, statement, *test.SQL)
		}
	}
}

// TestSubscriptionExistsStatementOrder pins the order of psycopg2's failures:
// the names in the query's order, then the custom pattern.
func TestSubscriptionExistsStatementOrder(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000000001"
	start := domain.CalendarDate{Year: 2025, Month: 9, Day: 1}
	end := domain.CalendarDate{Year: 2026, Month: 8, Day: 31}
	object := domain.RequestObject{{Name: "a", Value: json.Number("1")}}
	nul := "a\x00"
	cases := []struct {
		name   string
		names  []any
		custom *string
		want   string
	}{
		{name: "dict tax code", names: []any{"Mario", "Rossi", object}, want: "can't adapt type 'dict'"},
		{name: "dict first name", names: []any{object, "Rossi", "RSSMRA"}, want: "can't adapt type 'dict'"},
		{name: "nul first name beats a dict tax code", names: []any{nul, "Rossi", object},
			want: "A string literal cannot contain NUL (0x00) characters."},
		{name: "nul custom", names: []any{"Mario", "Rossi", "RSSMRA"}, custom: &nul,
			want: "A string literal cannot contain NUL (0x00) characters."},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			statement, err := subscriptionExistsStatement(association, test.names, start, end, test.custom, nil)
			var adapt *dto.Psycopg2AdaptError
			if !errors.As(err, &adapt) || adapt.Text != test.want || statement != "" {
				t.Fatalf("got (%q, %v), want %q", statement, err, test.want)
			}
		})
	}
}

// existsStatementTransaction is ExistsStatement reduced to its answer.
type existsStatementTransaction struct {
	domain.SubscriptionCreateTransaction
	statement string
	found     bool
	err       error
}

func (t *existsStatementTransaction) ExistsStatement(_ context.Context, statement string) (bool, error) {
	t.statement = statement
	return t.found, t.err
}

// TestSubscriptionExistsLiteral pins the two answers psycopg2's inlined
// statement produces: the client's adapt error and the server's error text
// (renew's 400 body), and the names the next lookup compares with.
func TestSubscriptionExistsLiteral(t *testing.T) {
	creates := &SubscriptionCreates{}
	association := "a1000000-0000-4000-8000-000000000001"
	start := domain.CalendarDate{Year: 2025, Month: 9, Day: 1}
	end := domain.CalendarDate{Year: 2026, Month: 8, Day: 31}
	ctx := context.Background()

	object := domain.RequestObject{{Name: "a", Value: json.Number("1")}}
	tx := &existsStatementTransaction{}
	_, err := creates.existsLiteral(ctx, tx, association, []any{"Mario", "Rossi", object}, start, end, nil, nil)
	var raised *domain.SubscriptionCreateRaised
	if !errors.As(err, &raised) || raised.Text != "can't adapt type 'dict'" || tx.statement != "" {
		t.Fatalf("got (%v) with statement %q, want psycopg2's adapt text", err, tx.statement)
	}

	tx = &existsStatementTransaction{err: &pgconn.PgError{Code: "42883", Message: "function upper(integer) does not exist",
		Hint: "No function matches the given name and argument types."}}
	_, err = creates.existsLiteral(ctx, tx, association, []any{"Mario", "Rossi", json.Number("5")}, start, end, nil, nil)
	if !errors.As(err, &raised) || tx.statement == "" || raised.Text != "function upper(integer) does not exist\n"+
		"HINT:  No function matches the given name and argument types.\n" {
		t.Fatalf("got %v for %q", err, tx.statement)
	}

	tx = &existsStatementTransaction{found: true}
	names, err := creates.existsLiteral(ctx, tx, association, []any{nil, "Rossi", []any{}}, start, end, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []*string{nil, ptr("Rossi"), ptr("{}")}
	for i := range want {
		if (names[i] == nil) != (want[i] == nil) || (names[i] != nil && *names[i] != *want[i]) {
			t.Fatalf("names %v, want %v", names, want)
		}
	}
	names, err = creates.existsLiteral(ctx, tx, association, []any{"Mario", "Rossi", []any{nil}}, start, end, nil, nil)
	if err != nil || names[2] == nil || *names[2] != "{NULL}" {
		t.Fatalf("got (%v, %v), want the list of NULLs as its array text", names, err)
	}
}

// TestSubscriptionDecimalInvalid pins str() of DecimalField.to_python's
// ValidationError for a float that is not a finite decimal.
func TestSubscriptionDecimalInvalid(t *testing.T) {
	cases := []struct {
		value float64
		want  string
	}{
		{value: math.NaN(), want: "['“nan” value must be a decimal number.']"},
		{value: math.Inf(1), want: "['“inf” value must be a decimal number.']"},
		{value: math.Inf(-1), want: "['“-inf” value must be a decimal number.']"},
	}
	for _, test := range cases {
		if got := subscriptionDecimalInvalid(test.value); got != test.want {
			t.Fatalf("%v: got %q, want %q", test.value, got, test.want)
		}
	}
}

// TestSubscriptionFeeIsZero pins get_membership_fee's `membership_fee == 0`:
// Decimal('NaN') == 0 is False, so a stored numeric NaN is no zero fee.
func TestSubscriptionFeeIsZero(t *testing.T) {
	cases := []struct {
		name string
		fee  *string
		want bool
	}{
		{name: "none", want: true},
		{name: "zero", fee: ptr("0"), want: true},
		{name: "zero decimals", fee: ptr("0.00"), want: true},
		{name: "negative zero", fee: ptr("-0.00"), want: true},
		{name: "fee", fee: ptr("25.00"), want: false},
		{name: "nan", fee: ptr("NaN"), want: false},
		{name: "infinity", fee: ptr("Infinity"), want: false},
		{name: "not a number", fee: ptr("abc"), want: false},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if got := subscriptionFeeIsZero(test.fee); got != test.want {
				t.Fatalf("got %v, want %v", got, test.want)
			}
		})
	}
}

// TestSubscriptionNumericColumns pins the sentinel the fee rules compare (an
// int outside int64 matches none of them, like 0) and the smallint columns.
func TestSubscriptionNumericColumns(t *testing.T) {
	if got := subscriptionInt64(big.NewInt(2)); got != 2 {
		t.Fatalf("got %d, want 2", got)
	}
	if got := subscriptionInt64(big.NewInt(40000)); got != 40000 {
		t.Fatalf("got %d, want the int64 value (the INSERT reports it)", got)
	}
	huge, ok := new(big.Int).SetString("100000000000000000000", 10)
	if !ok {
		t.Fatal("bad integer")
	}
	if got := subscriptionInt64(huge); got != 0 {
		t.Fatalf("got %d, want the 0 sentinel", got)
	}
	bounds := []struct {
		value int64
		want  bool
	}{
		{value: 0, want: true},
		{value: math.MinInt16, want: true},
		{value: math.MaxInt16, want: true},
		{value: math.MinInt16 - 1},
		{value: math.MaxInt16 + 1},
		{value: 40000},
	}
	for _, test := range bounds {
		if got := subscriptionSmallint(big.NewInt(test.value)); got != test.want {
			t.Fatalf("%d: got %v, want %v", test.value, got, test.want)
		}
	}
	if subscriptionSmallint(huge) {
		t.Fatal("an int past int64 is no smallint")
	}
}

// TestSubscriptionStoredNonFinite pins float() of a stored numeric column:
// PostgreSQL's NaN and infinities are Python's float(Decimal(...)) values.
func TestSubscriptionStoredNonFinite(t *testing.T) {
	cases := map[string]float64{"NaN": math.NaN(), "Infinity": math.Inf(1), "-Infinity": math.Inf(-1)}
	for text, want := range cases {
		got, err := subscriptionCreateFloat(json.Number(text))
		if err != nil {
			t.Fatalf("%q: %v", text, err)
		}
		if math.IsNaN(want) {
			if !math.IsNaN(got) {
				t.Fatalf("%q: got %v, want nan", text, got)
			}
			continue
		}
		if got != want {
			t.Fatalf("%q: got %v, want %v", text, got, want)
		}
	}
	if number, err := subscriptionCreateFloat(json.Number("25.00")); err != nil || number != 25 {
		t.Fatalf("stored fee: got (%v, %v)", number, err)
	}
}
