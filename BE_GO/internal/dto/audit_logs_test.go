package dto

import (
	"encoding/json"
	"errors"
	"math"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type noResolver struct{}

func (noResolver) ResolveUUID(string, string) (any, error) { return nil, nil }

func TestAuditIsUUIDAcceptsTrailingNewlineLikePythonDollar(t *testing.T) {
	id := "C0A5E001-0000-4000-8000-000000996004"
	if !AuditIsUUID(id) || !AuditIsUUID(id+"\n") || AuditIsUUID(id+"\n\n") || AuditIsUUID(id+" ") || AuditIsUUID(5) {
		t.Fatal("is_uuid mismatch")
	}
}

func TestPythonStrptimeDate(t *testing.T) {
	cases := map[string]string{
		"2026-10-02": "2026-10-02", "2026-9-30": "2026-09-30", "2026-10-1": "2026-10-01", "2026-10- 5": "2026-10-05",
		"٢٠٢٦-10-02": "2026-10-02", "2026-1٢-02": "", "2026-١٠-02": "", "2026-02-30": "", "0000-01-01": "",
		"2026-10-01x": "", "2026-10-0": "", "2026-101-01": "", "": "",
	}
	for input, want := range cases {
		got, ok := PythonStrptimeDate(input)
		if (want == "") == ok || (ok && got.Format("2006-01-02") != want) || (ok && got.Location() != time.UTC) {
			t.Errorf("%q: got %v %v, want %q", input, got, ok, want)
		}
	}
}

func TestPythonIntText(t *testing.T) {
	for input, want := range map[string]string{" 2_0 ": "20", "+1": "1", "-4": "-4", "２": "2", "007": "7",
		"99999999999999999999999": "99999999999999999999999"} {
		got, ok := PythonIntText(input)
		if !ok || got.String() != want {
			t.Errorf("%q: got %v %v", input, got, ok)
		}
	}
	for _, input := range []string{"", "1.0", "_1", "1_", "1__0", "abc", "+", "1 2"} {
		if _, ok := PythonIntText(input); ok {
			t.Errorf("%q parsed", input)
		}
	}
}

func TestAuditFromISOFormat(t *testing.T) {
	ok := map[string]string{"2026-09-15T18:30:00Z": "15/09/2026 18:30", "2026-10-01": "01/10/2026 00:00",
		"2026-10-01 07:05": "01/10/2026 07:05", "2026-10-01T07:05:09.123+02:00": "01/10/2026 07:05"}
	for input, want := range ok {
		moment, err := AuditFromISOFormat(input)
		if err != nil || moment.DateTime != want {
			t.Errorf("%q: %v %v", input, moment, err)
		}
	}
	for _, input := range []string{"domani", "2026-13-01", "2026-02-30", "2026-10-01T23:60", "x2026-10-01"} {
		if _, err := AuditFromISOFormat(input); !IsAuditISOValueError(err) {
			t.Errorf("%q: want ValueError, got %v", input, err)
		}
	}
	for input, want := range map[string]string{"20261001": "01/10/2026 00:00", "2026-W40-1": "28/09/2026 00:00",
		"2026-10-01T07:05:09.1": "01/10/2026 07:05", "2026-10-01x07:05+0530": "01/10/2026 07:05"} {
		if moment, err := AuditFromISOFormat(input); err != nil || moment.DateTime != want {
			t.Errorf("%q: %v %v", input, moment, err)
		}
	}
	if _, err := AuditFromISOFormat("2026-10-01T24:00"); !IsAuditISOValueError(err) {
		t.Errorf("24:00: want ValueError, got %v", err)
	}
	// Years below 1000: strftime('%Y') padding depends on the C library, so they stay refused.
	for _, input := range []string{"0999-01-01"} {
		if _, err := AuditFromISOFormat(input); !errors.Is(err, ErrAuditNotPorted) {
			t.Errorf("%q: want refusal, got %v", input, err)
		}
	}
	if _, err := AuditFromISOFormat(json.Number("5")); !errors.Is(err, ErrAuditUnhandled) {
		t.Error("non-str start must be the unhandled AttributeError")
	}
}

func TestAuditJSONLoadsRefusals(t *testing.T) {
	if _, _, err := AuditJSONLoads(`[NaN]`); !errors.Is(err, ErrAuditNotPorted) {
		t.Error("NaN must be refused")
	}
	if _, _, err := AuditJSONLoads(`["\ud800"]`); !errors.Is(err, ErrAuditNotPorted) {
		t.Error("lone surrogate must be refused")
	}
	if value, decoded, err := AuditJSONLoads(`["😀"]`); err != nil || !decoded || value.([]any)[0] != "😀" {
		t.Error("surrogate pair must decode")
	}
	huge := "[1" + string(make([]byte, 0)) + ""
	for i := 0; i < 4301; i++ {
		huge += "0"
	}
	if _, _, err := AuditJSONLoads(huge + "]"); !errors.Is(err, ErrAuditUnhandled) {
		t.Error("over 4300 digits is Python's ValueError")
	}
	if _, decoded, err := AuditJSONLoads(`[not json`); err != nil || decoded {
		t.Error("invalid JSON is a JSONDecodeError")
	}
	if _, decoded, err := AuditJSONLoads(`[1] x`); err != nil || decoded {
		t.Error("extra data is a JSONDecodeError")
	}
}

func TestAuditTitleRefusesNonASCII(t *testing.T) {
	if label, err := auditFieldLabel("camps_and_retreats"); err != nil || label != "Camps And Retreats" {
		t.Error(label, err)
	}
	if _, err := auditFieldLabel("straße"); !errors.Is(err, ErrAuditNotPorted) {
		t.Error("non-ASCII title must be refused")
	}
	changes := domain.RequestObject{{Name: "straße", Value: []any{"a", "b"}}}
	if _, err := AuditDisplayChanges(changes, noResolver{}); !errors.Is(err, ErrAuditNotPorted) {
		t.Error("display of a non-ASCII field key must be refused")
	}
}

func TestAuditFormatterExceptionsEscape(t *testing.T) {
	for field, value := range map[string]any{"additional_fields": `[{"props": "x"}]`, "collaborator_permissions": `[1]`,
		"events": `[{"title": 5}]`} {
		if _, err := AuditEnrich(value, field, noResolver{}); !errors.Is(err, ErrAuditUnhandled) {
			t.Errorf("%s: %v", field, err)
		}
	}
}

func TestAuditFiniteJSON(t *testing.T) {
	if AuditFiniteJSON([]any{json.Number("1e400")}) || !AuditFiniteJSON([]any{json.Number("1"), json.Number("2.5")}) {
		t.Fatal("finite check")
	}
}

func TestAuditStoredDate(t *testing.T) {
	year, dayMonth := "2026", "15/09"
	if date, ok, err := AuditStoredDate(&year, &dayMonth); !ok || err != nil || date != "15/09/2026" {
		t.Fatal(date, ok, err)
	}
	for _, y := range []string{"0", "-5", "10000", "Infinity"} {
		if _, ok, err := AuditStoredDate(&y, &dayMonth); ok || err != nil {
			t.Errorf("%s must be an unloadable row", y)
		}
	}
	early := "999"
	if _, _, err := AuditStoredDate(&early, &dayMonth); !errors.Is(err, ErrAuditNotPorted) {
		t.Error("years below 1000 are refused")
	}
}

func TestAuditPythonJSONLoadsConstantsAndSurrogates(t *testing.T) {
	value, ok, err := auditPythonJSONLoads(`[NaN, -Infinity, Infinity, {"a": "NaN"}, "\ud800"]`)
	if err != nil || !ok {
		t.Fatalf("loads: %v %v", ok, err)
	}
	list := value.([]any)
	if f, _ := list[0].(float64); !math.IsNaN(f) {
		t.Fatalf("NaN: %#v", list[0])
	}
	if f, _ := list[1].(float64); !math.IsInf(f, -1) {
		t.Fatalf("-Infinity: %#v", list[1])
	}
	if f, _ := list[2].(float64); !math.IsInf(f, 1) {
		t.Fatalf("Infinity: %#v", list[2])
	}
	if object := list[3].(domain.RequestObject); object[0].Value != "NaN" {
		t.Fatalf("string left alone: %#v", object)
	}
	if AuditResponseEncodable(value) {
		t.Fatal("non-finite values and surrogates are not encodable")
	}
	if text, ok, err := auditPythonJSONLoads(`[NaNx]`); ok || err != nil {
		t.Fatalf("NaNx: %v %v %v", text, ok, err)
	}
	if _, ok, _ := auditPythonJSONLoads(`["😀", 1.5]`); !ok {
		t.Fatal("paired surrogates and floats load")
	}
}
