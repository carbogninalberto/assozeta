package service

import (
	"encoding/json"
	"errors"
	"math"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestSubscriptionCreatePlanFloatIDs drives extract_the_*_plan's
// str(plan['id']) == str(plan_id) comparison over JSON float ids: Python's
// str() renders 1.0 as '1.0' and 1e16 as '1e+16'.
func TestSubscriptionCreatePlanFloatIDs(t *testing.T) {
	raw := []byte(`[{"id": 1.0, "name": "Virgola"}, {"id": 1e16, "name": "Grande"}, {"id": "testo", "name": "Testo"}]`)
	cases := []struct {
		name string
		id   any
		want string
	}{
		{name: "stored and requested float", id: json.Number("1.0"), want: "Virgola"},
		{name: "requested text of the float", id: "1.0", want: "Virgola"},
		{name: "large exponent", id: json.Number("1e16"), want: "Grande"},
		{name: "large exponent as text", id: "1e+16", want: "Grande"},
		{name: "text id", id: "testo", want: "Testo"},
		{name: "no plan", id: json.Number("2.0")},
	}
	for _, c := range cases {
		plan, err := subscriptionCreatePlan(raw, c.id)
		if err != nil {
			t.Errorf("%s: %v", c.name, err)
			continue
		}
		if c.want == "" {
			if plan != nil {
				t.Errorf("%s: got a plan, want none", c.name)
			}
			continue
		}
		name, _ := plan.Lookup("name")
		if name != c.want {
			t.Errorf("%s: got %v, want %q", c.name, name, c.want)
		}
	}
}

// TestSubscriptionCreateFloatText is float(text): the spellings Python reads
// beyond a plain decimal are accepted; anything else raises (the 500 page).
func TestSubscriptionCreateFloatText(t *testing.T) {
	numbers := map[string]float64{" 12.5 ": 12.5, "1e2": 100, "1_000": 1000, "+3": 3, ".5": 0.5, "5.": 5,
		"١٢": 12, "inf": math.Inf(1), "-Infinity": math.Inf(-1), "nan": math.NaN()}
	for text, want := range numbers {
		got, err := subscriptionCreateFloat(text)
		if err != nil {
			t.Errorf("%q: %v", text, err)
			continue
		}
		if math.IsNaN(want) {
			if !math.IsNaN(got) {
				t.Errorf("%q: got %v, want nan", text, got)
			}
			continue
		}
		if got != want {
			t.Errorf("%q: got %v, want %v", text, got, want)
		}
	}
	for _, text := range []string{"", "non-un-numero", "1__0", "1e", "--1", "0x10"} {
		if _, err := subscriptionCreateFloat(text); !errors.Is(err, domain.ErrSubscriptionCreateServer) {
			t.Errorf("%q: got %v, want the server error", text, err)
		}
	}
	if _, err := subscriptionCreateFloat(nil); !errors.Is(err, domain.ErrSubscriptionCreateServer) {
		t.Errorf("None: got %v, want the server error", err)
	}
}

// TestSubscriptionCreateAmountRounding is DecimalField(max_digits=9,
// decimal_places=2): the decimal is the float rounded to nine significant
// digits (halves even) and the column rounds it to two places (halves away
// from zero), so 25.005 is stored 25.01 while its audit text stays 25.005.
func TestSubscriptionCreateAmountRounding(t *testing.T) {
	// A runtime float addition (Go evaluates the untyped constant 0.1+0.2 exactly).
	sum := 0.1
	sum += 0.2
	cases := []struct {
		value            float64
		stored, rendered string
	}{
		{value: 25, stored: "25.00", rendered: "25.0"},
		{value: 52.5, stored: "52.50", rendered: "52.5"},
		{value: sum, stored: "0.30", rendered: "0.30000000000000004"},
		{value: 25.005, stored: "25.01", rendered: "25.005"},
		// Nine significant digits then two places: the sent decimal is 25.0050000.
		{value: 25.0049999999999, stored: "25.01", rendered: "25.0049999999999"},
		// Exactly half: PostgreSQL rounds away from zero.
		{value: 0.125, stored: "0.13", rendered: "0.125"},
	}
	for _, c := range cases {
		if got := dto.PythonFloatText(c.value); got != c.rendered {
			t.Errorf("%v: rendered %q, want %q", c.value, got, c.rendered)
		}
		if _, ok := dto.SubscriptionCreateDecimalValue(c.value); !ok {
			t.Errorf("%v: the decimal conversion must not fail", c.value)
		}
		got, ok := dto.SubscriptionCreateDecimalStored(c.value)
		if !ok || got != c.stored {
			t.Errorf("%v: got %q %v, want %q", c.value, got, ok, c.stored)
		}
	}
	for _, value := range []float64{math.Inf(1), math.Inf(-1), math.NaN()} {
		if _, ok := dto.SubscriptionCreateDecimalValue(value); ok {
			t.Errorf("%v: the conversion must fail", value)
		}
		if _, ok := dto.SubscriptionCreateDecimalStored(value); ok {
			t.Errorf("%v: the stored value must fail", value)
		}
	}
}

// TestSubscriptionCreateMembershipNameText is a CharField's str() of a
// non-string membership plan name.
func TestSubscriptionCreateMembershipNameText(t *testing.T) {
	cases := map[string]any{"42": json.Number("42"), "42.5": json.Number("42.5"), "True": true,
		"[1, 2]":   []any{json.Number("1"), json.Number("2")},
		"{'a': 1}": domain.RequestObject{{Name: "a", Value: json.Number("1")}}}
	for want, value := range cases {
		if got := dto.PythonStr(value); got != want {
			t.Errorf("%#v: got %q, want %q", value, got, want)
		}
	}
}
