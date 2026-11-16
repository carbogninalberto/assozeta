package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
)

// Python's round(float(total) / 100, 2) for the totals the cases below sum
// (CPython 3.13, checked with $PY).
func TestPaymentStatsStripeFeesArithmetic(t *testing.T) {
	huge := strings.Repeat("9", 401)
	cases := map[string]struct {
		amounts []string
		want    float64
		zero    bool
	}{
		"ints":               {[]string{"100", "250"}, 3.5, false},
		"float joins int":    {[]string{"100", "0.5"}, 1.0, false},
		"float first":        {[]string{"0.5", "100"}, 1.0, false},
		"big int in range":   {[]string{"9007199254740993"}, 90071992547409.92, false},
		"big int 10^308":     {[]string{strings.Repeat("9", 308)}, 1e306, false},
		"bool":               {[]string{"true", "true"}, 0.02, false},
		"big int overflow":   {[]string{huge}, 0, true},
		"huge float join":    {[]string{"1.5", huge}, 0, true},
		"huge then float":    {[]string{huge, "1.5"}, 0, true},
		"string amount":      {[]string{`"100"`}, 0, true},
		"null amount":        {[]string{"null"}, 0, true},
		"list amount":        {[]string{"[1]"}, 0, true},
		"overflowing float":  {[]string{"1e400"}, math.Inf(1), false},
		"negative infinity":  {[]string{"-1e400"}, math.Inf(-1), false},
		"two fee details":    {[]string{"100", "250", "50"}, 4.0, false},
		"rounds half even":   {[]string{"267.5"}, 2.67, false},
		"zero amount":        {[]string{"0"}, 0, false},
		"fractional amount":  {[]string{"1.5"}, 0.01, false},
		"float sum overflow": {[]string{"1e308", "1e308"}, math.Inf(1), false},
		"max float overflow": {[]string{"1.7976931348623157e308", "1.7976931348623157e308"}, math.Inf(1), false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			page := feePage(tc.amounts, "charge")
			fee, err, _ := runFees(t, feeResponse{200, page})
			if tc.zero {
				if !errors.Is(err, errPaymentStatsStripeZero) {
					t.Fatalf("fees = %v, %v; want the zero-charges exception", fee, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("fees: %v", err)
			}
			if fee != tc.want && !(math.IsInf(tc.want, 0) && math.IsInf(fee, 0) && math.Signbit(fee) == math.Signbit(tc.want)) {
				t.Fatalf("fees = %v, want %v", fee, tc.want)
			}
		})
	}
}

func TestPaymentStatsStripeSkipsPayoutsAndOtherFees(t *testing.T) {
	pages := map[string]string{
		"payout":       feePage([]string{"100"}, "payout"),
		"other fee":    feePageCustom(`[{"type":"charge","fee_details":[{"type":"application_fee","amount":100}]}]`),
		"mixed":        feePageCustom(`[{"type":"charge","fee_details":[{"type":"application_fee","amount":100},{"type":"stripe_fee","amount":250}]}]`),
		"payout first": feePageCustom(`[{"type":"payout","fee_details":[{"type":"stripe_fee","amount":100}]}]`),
	}
	wants := map[string]float64{"payout": 0, "other fee": 0, "mixed": 2.5, "payout first": 0}
	for name, page := range pages {
		t.Run(name, func(t *testing.T) {
			fee, err, _ := runFees(t, feeResponse{200, page})
			if err != nil {
				t.Fatalf("fees: %v", err)
			}
			if fee != wants[name] {
				t.Fatalf("fees = %v, want %v", fee, wants[name])
			}
		})
	}
}

// A JSON reader that yields float64 (the package's own uses json.Number)
// reaches the same arithmetic.
func TestPaymentStatsStripeSumFloat64(t *testing.T) {
	for name, tc := range map[string]struct {
		values []any
		want   float64
		ok     bool
	}{
		"nan":       {[]any{float64(math.NaN())}, math.NaN(), true},
		"infinity":  {[]any{float64(math.Inf(1))}, math.Inf(1), true},
		"json nan":  {[]any{json.Number("NaN")}, math.NaN(), true},
		"json inf":  {[]any{json.Number("Infinity")}, math.Inf(1), true},
		"huge int":  {[]any{json.Number(strings.Repeat("9", 401))}, 0, false},
		"float int": {[]any{json.Number("1e308"), json.Number("1e308")}, math.Inf(1), true},
	} {
		t.Run(name, func(t *testing.T) {
			sum := newPaymentStatsSum()
			for _, value := range tc.values {
				if !sum.add(value) {
					t.Fatalf("add(%v) raised", value)
				}
			}
			got, ok := sum.charge()
			if ok != tc.ok {
				t.Fatalf("charge ok = %v, want %v", ok, tc.ok)
			}
			if !ok {
				return
			}
			if math.IsNaN(tc.want) != math.IsNaN(got) || (!math.IsNaN(tc.want) && got != tc.want) {
				t.Fatalf("charge = %v, want %v", got, tc.want)
			}
		})
	}
}

// starting_after=data[-1].id is encoded as stripe-python encodes the value
// (str(int), str(bool), a dict's nested keys); the request outcome decides.
func TestPaymentStatsStripePaginationID(t *testing.T) {
	first := func(id string) string {
		return `{"data":[{"type":"charge","fee_details":[{"type":"stripe_fee","amount":100}],"id":` + id +
			`}],"has_more":true,"url":"/v1/balance_transactions"}`
	}
	empty := `{"data":[],"has_more":false}`
	cases := map[string]struct {
		first    string
		second   feeResponse
		want     float64
		zero     bool
		queries  int
		fragment string
	}{
		"int":           {first("123"), feeResponse{200, empty}, 1, false, 2, "starting_after=123"},
		"bool":          {first("true"), feeResponse{200, empty}, 1, false, 2, "starting_after=True"},
		"false":         {first("false"), feeResponse{200, empty}, 0, true, 1, ""},
		"zero":          {first("0"), feeResponse{200, empty}, 0, true, 1, ""},
		"null":          {first("null"), feeResponse{200, empty}, 0, true, 1, ""},
		"dict":          {first(`{"a":1,"b":[2,3]}`), feeResponse{200, empty}, 1, false, 2, "starting_after%5Ba%5D=1&starting_after%5Bb%5D%5B0%5D=2&starting_after%5Bb%5D%5B1%5D=3"},
		"list":          {first(`[4,5]`), feeResponse{200, empty}, 1, false, 2, "starting_after%5B0%5D=4&starting_after%5B1%5D=5"},
		"string":        {first(`"txn_1"`), feeResponse{200, empty}, 1, false, 2, "starting_after=txn_1"},
		"request error": {first("123"), feeResponse{400, `{"error":{"type":"invalid_request_error","message":"bad starting_after"}}`}, 0, true, 2, ""},
		"missing id": {`{"data":[{"type":"charge","fee_details":[{"type":"stripe_fee","amount":100}]}],"has_more":true,` +
			`"url":"/v1/balance_transactions"}`, feeResponse{200, empty}, 0, true, 1, ""},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			fee, err, queries := runFees(t, feeResponse{200, tc.first}, tc.second)
			if len(queries) != tc.queries {
				t.Fatalf("requests = %d (%v), want %d", len(queries), queries, tc.queries)
			}
			if tc.zero {
				if !errors.Is(err, errPaymentStatsStripeZero) {
					t.Fatalf("fees = %v, %v; want the zero-charges exception", fee, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("fees: %v", err)
			}
			if fee != tc.want {
				t.Fatalf("fees = %v, want %v", fee, tc.want)
			}
			if len(queries) > 1 && !strings.Contains(queries[1], tc.fragment) {
				t.Fatalf("second query %q does not contain %q", queries[1], tc.fragment)
			}
		})
	}
}

// feePage is a balance transaction page with the amounts as raw JSON tokens.
func feePage(amounts []string, kind string) string {
	var details []string
	for _, amount := range amounts {
		details = append(details, `{"type":"stripe_fee","amount":`+amount+`}`)
	}
	return feePageCustom(`[{"type":"` + kind + `","fee_details":[` + strings.Join(details, ",") + `]}]`)
}

func feePageCustom(items string) string {
	return `{"data":` + items + `,"has_more":false,"url":"/v1/balance_transactions"}`
}

type feeResponse struct {
	status int
	body   string
}

// runFees serves the responses in order and returns the fee, the error and the
// request URIs the session sent.
func runFees(t *testing.T, responses ...feeResponse) (float64, error, []string) {
	t.Helper()
	var mu sync.Mutex
	var queries []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		index := len(queries)
		queries = append(queries, r.URL.String())
		mu.Unlock()
		response := feeResponse{status: 500, body: `{"error":{"type":"api_error"}}`}
		if index < len(responses) {
			response = responses[index]
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(response.status)
		fmt.Fprint(w, response.body)
	}))
	defer server.Close()
	client := stripeapi.NewClient(server.URL)
	session := client.Session("sk_test")
	fee, err := (&PaymentStatsStripe{}).fees(context.Background(), session, 100, 200)
	mu.Lock()
	defer mu.Unlock()
	return fee, err, append([]string(nil), queries...)
}

// A non-finite sum is cached and then fails DRF's strict JSON rendering (the
// 500 page); finite charges are answered as they are.
func TestPaymentStatsStripeNonFiniteChargesFailToRender(t *testing.T) {
	for name, value := range map[string]float64{"inf": math.Inf(1), "-inf": math.Inf(-1), "nan": math.NaN()} {
		if _, err := paymentStatsRenderable(&domain.PaymentStripeCharges{Total: value, StripeFee: value}); !errors.Is(err, errPaymentStatsNonFinite) {
			t.Errorf("%s: err = %v", name, err)
		}
	}
	charges := &domain.PaymentStripeCharges{Total: 2.67, StripeFee: 2.67}
	if got, err := paymentStatsRenderable(charges); err != nil || got != charges {
		t.Fatalf("finite charges: %v, %v", got, err)
	}
}
