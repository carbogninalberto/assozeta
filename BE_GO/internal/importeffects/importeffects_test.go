package importeffects

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

// testdata/pyset.json holds CPython 3.13 iteration orders of uuid.UUID sets
// built by add() (random keys with repeats).
func TestPySetOrderMatchesCPython(t *testing.T) {
	data, err := os.ReadFile("testdata/pyset.json")
	if err != nil || len(data) == 0 {
		t.Fatalf("goldens: %v", err)
	}
	var cases []struct {
		In  []string `json:"in"`
		Out []string `json:"out"`
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) == 0 {
		t.Fatalf("goldens: %v", err)
	}
	for i, c := range cases {
		if got := pySetOrder(c.In); !reflect.DeepEqual(got, c.Out) {
			t.Fatalf("case %d (%d keys): got %v want %v", i, len(c.In), got, c.Out)
		}
	}
}

func TestPlanLoads(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	if plan.Model("Payment").Save != "payment" || plan.Model("User").Str != "user" || !plan.Model("Folder").IsMPTT {
		t.Fatal("unexpected plan")
	}
}

func TestAuditRendering(t *testing.T) {
	plan, _ := LoadPlan()
	payment := plan.Model("Payment")
	moment := time.Date(2026, 1, 2, 4, 4, 5, 123456000, time.FixedZone("", 3600))
	cases := []struct {
		field string
		value Value
		text  string
		keep  bool
	}{
		{"payment_date", DateTimeValue(moment), "2026-01-02 03:04:05.123456", true},
		{"payment_date", NoneValue, "None", true},
		{"invoice", NoneValue, "None", true},
		{"description", NoneValue, "None", false},
		{"description", StrValue("None"), "None", false},
		{"paid", BoolValue(true), "True", true},
		{"amount", Value{Kind: Decimal, S: "10.50"}, "10.50", true},
		{"meta", NoneValue, "null", true},
	}
	for _, c := range cases {
		text, keep, err := auditCreate(payment.Field(c.field), c.value)
		if err != nil || text != c.text || keep != c.keep {
			t.Fatalf("%s: %q %v %v", c.field, text, keep, err)
		}
	}
}

func TestSeasonRange(t *testing.T) {
	leap := time.Date(2028, 3, 1, 0, 0, 0, 0, time.UTC)
	if seasonRangeValid(leap, 29, 2, false, 12, 31) {
		t.Fatal("February 29 of the previous year raises")
	}
	if !seasonRangeValid(leap, 1, 9, false, 12, 31) {
		t.Fatal("September season")
	}
}
