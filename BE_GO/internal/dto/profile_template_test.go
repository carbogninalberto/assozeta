package dto

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"strconv"
	"testing"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// testdata/python_upper.json is CPython 3.13.16 str.upper() for every accepted
// code point (TESTS/adapters/django/bs4_prettify_corpus.py --write-editor).
func TestProfileTemplateUpperAgainstPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_upper.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Accepted int               `json:"accepted"`
		Upper    map[string]string `json:"upper"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	accepted := 0
	for r := rune(0); r <= utf8.MaxRune; r++ {
		if r >= 0xd800 && r <= 0xdfff || !profileTemplateUpperSupported(r) {
			continue
		}
		accepted++
		want, ok := golden.Upper[strconv.Itoa(int(r))]
		if !ok {
			want = string(r)
		}
		if got, ok := ProfileTemplateUpper(string(r)); !ok || got != want {
			t.Fatalf("U+%04X: %q, want %q", r, got, want)
		}
	}
	if accepted != golden.Accepted {
		t.Fatalf("accepted %d, golden %d", accepted, golden.Accepted)
	}
	// Special casing and the characters outside the golden's accepted ranges (CPython 3.13.16).
	for in, want := range map[string]string{"ß": "SS", "ŉ": "ʼN", "ǰ": "J̌", "ﬁ": "FI", "ꭰ": "Ꭰ", "\u0390": "\u0399\u0308\u0301", "ᾳ": "ΑΙ", "ǆ": "Ǆ",
		"ﬓ": "ՄՆ", "\ufffd": "\ufffd"} {
		if got, ok := ProfileTemplateUpper(in); !ok || got != want {
			t.Fatalf("%q: %q, want %q", in, got, want)
		}
	}
	if _, ok := ProfileTemplateUpper("a\xffb"); ok {
		t.Fatal("invalid UTF-8 accepted")
	}
}

func profileTemplateUpperSupported(r rune) bool {
	switch {
	case r == 0xdf || r == 0x149 || r == 0x1f0 || r == unicode.ReplacementChar:
		return false
	case r >= 0x20 && r < 0x7f, r >= 0xa0 && r < 0x250, r >= 0x2000 && r < 0x2c00, r >= 0x3000 && r < 0xa000,
		r >= 0x1f000 && r < 0x1fb00, r == '\t', r == '\n', r == '\r':
		return true
	}
	return false
}

func TestProfileTemplateFloat(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  float64
		err   error
	}{
		{true, 1, nil}, {false, 0, nil}, {json.Number("20"), 20, nil}, {json.Number("12.345"), 12.345, nil},
		{json.Number("1e3"), 1000, nil}, {"1e3", 1000, nil}, {".5", 0.5, nil}, {"5.", 5, nil}, {"+5", 5, nil},
		{"-3", -3, nil}, {"INF", math.Inf(1), nil}, {"-Infinity", math.Inf(-1), nil},
		{"12,5", 0, ErrProfileTemplateNotPorted}, {"", 0, ErrProfileTemplateNotPorted}, {" 5", 0, ErrProfileTemplateNotPorted},
		{"1_0", 0, ErrProfileTemplateNotPorted}, {json.Number("1e400"), 0, ErrProfileTemplateNotPorted},
		{json.Number("1234567890123456"), 0, ErrProfileTemplateNotPorted},
		{nil, 0, ErrProfileTemplatePython}, {[]any{}, 0, ErrProfileTemplatePython}, {domain.RequestObject{}, 0, ErrProfileTemplatePython},
	} {
		got, err := ProfileTemplateFloat(tc.value)
		if !errors.Is(err, tc.err) || err == nil && got != tc.want {
			t.Errorf("%#v: %v %v", tc.value, got, err)
		}
	}
	if got, err := ProfileTemplateFloat("nan"); err != nil || !math.IsNaN(got) {
		t.Error("nan")
	}
}

func TestProfileTemplateDecimalLiteral(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  string
		ok    bool
	}{
		// Context(prec=9).create_decimal_from_float rounds half to even.
		{json.Number("1234567.125"), "1.23456712e+06", true}, {json.Number("1234567.375"), "1.23456738e+06", true},
		{json.Number("12.345"), "1.23450000e+01", true}, {json.Number("20"), "20", true}, {json.Number("-0.0"), "-0.00000000e+00", true},
		{true, "1", true}, {"1234567.125", "1234567.125", true}, {"+.5", "0.5", true}, {"5.", "5.0", true},
		{"-5.E3", "-5.0e3", true}, {"inf", "", false}, {"NaN", "", false},
	} {
		got, ok := ProfileTemplateDecimalLiteral(tc.value)
		if got != tc.want || ok != tc.ok {
			t.Errorf("%#v: %q %v", tc.value, got, ok)
		}
	}
}

func TestProfileTemplateBooleanAndStr(t *testing.T) {
	for _, tc := range []struct {
		value     any
		want, ok  bool
		auditText string
	}{
		{"t", true, true, "t"}, {"True", true, true, "True"}, {"1", true, true, "1"}, {"f", false, true, "f"},
		{"False", false, true, "False"}, {"0", false, true, "0"}, {"true", false, false, "true"},
		{json.Number("1"), true, true, "1"}, {json.Number("0"), false, true, "0"}, {json.Number("-0"), false, true, "0"},
		{json.Number("1.0"), true, true, "1.0"}, {json.Number("2"), false, false, "2"}, {false, false, true, "False"},
	} {
		got, ok := ProfileTemplateBooleanValue(tc.value)
		if got != tc.want || ok != tc.ok || ProfileTemplateStr(tc.value) != tc.auditText {
			t.Errorf("%#v: %v %v %q", tc.value, got, ok, ProfileTemplateStr(tc.value))
		}
	}
}

func profileTemplateTestPayload(changes ...domain.RequestField) domain.RequestObject {
	data := domain.RequestObject{}
	for _, key := range profileTemplateKeys {
		data = append(data, domain.RequestField{Name: key})
	}
	for _, change := range changes {
		data = PythonSet(data, change.Name, change.Value)
	}
	return data
}

func TestPlanProfileTemplate(t *testing.T) {
	plan, err := PlanProfileTemplate(profileTemplateTestPayload(
		domain.RequestField{Name: "additional_sections", Value: []any{
			domain.RequestObject{{Name: "name", Value: "privacy è"}, {Name: "text", Value: "<p>x</p>"}, {Name: "extra", Value: true}},
			domain.RequestObject{{Name: "name", Value: ""}, {Name: "text", Value: "<p>y</p>"}},
			domain.RequestObject{{Name: "name", Value: "solo"}},
		}},
		domain.RequestField{Name: "subscription_fee_plans", Value: []any{
			domain.RequestObject{{Name: "name", Value: "A"}, {Name: "subscription_fee", Value: ""}},
			domain.RequestObject{{Name: "name", Value: nil}, {Name: "subscription_fee", Value: "not a number"}},
			domain.RequestObject{{Name: "name", Value: "B"}, {Name: "subscription_fee", Value: json.Number("-1")}},
		}},
		domain.RequestField{Name: "membership_fee_plans", Value: ""},
		domain.RequestField{Name: "multiple_membership_fee", Value: true},
		domain.RequestField{Name: "membership_fee", Value: ""},
		domain.RequestField{Name: "logo", Value: "data:,x"},
	))
	if err != nil {
		t.Fatal(err)
	}
	audit := map[string]string{}
	for _, field := range plan.Fields {
		audit[field.Name] = ProfileTemplateAudit(field)
	}
	want := map[string]string{
		"additional_sections":     "[{\"extra\": true, \"name\": \"PRIVACY \\u00c8\", \"text\": \"<p>\\n x\\n</p>\\n\"}]",
		"subscription_fee_plans":  `[{"name": "A", "subscription_fee": 0.0}]`,
		"membership_fee_plans":    `[]`,
		"multiple_membership_fee": "False", "membership_fee": "None",
		"enabled_for": "null", "additional_fields": "null", "stripe_available_methods": "null",
		"invoice_template": "None", "subscription_template": "None", "extra_text_invoices": "None",
	}
	if len(audit) != len(want) || plan.Logo == nil || *plan.Logo != "data:,x" {
		t.Fatalf("%v %v", audit, plan.Logo)
	}
	for name, value := range want {
		if audit[name] != value {
			t.Errorf("%s: %s, want %s", name, audit[name], value)
		}
	}
	for _, tc := range []struct {
		field domain.RequestField
		err   error
	}{
		{domain.RequestField{Name: "additional_sections", Value: "abc"}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "additional_sections", Value: json.Number("5")}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "additional_sections", Value: []any{domain.RequestObject{{Name: "name", Value: json.Number("5")},
			{Name: "text", Value: "<p>x</p>"}}}}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "regulation", Value: json.Number("5")}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "regulation", Value: "<!-- c -->"}, ErrProfileTemplateNotPorted},
		{domain.RequestField{Name: "subscription_fee_plans", Value: []any{"x"}}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "membership_fee_plans", Value: []any{domain.RequestObject{{Name: "membership_fee", Value: json.Number("1")}}}},
			ErrProfileTemplatePython},
		{domain.RequestField{Name: "logo", Value: json.Number("5")}, ErrProfileTemplateNotPorted},
		{domain.RequestField{Name: "invoice_template", Value: []any{}}, ErrProfileTemplateNotPorted},
		{domain.RequestField{Name: "enabled_for", Value: []any{"\xed\xa0\x80"}}, ErrProfileTemplateNotPorted},
	} {
		if _, err := PlanProfileTemplate(profileTemplateTestPayload(tc.field)); !errors.Is(err, tc.err) {
			t.Errorf("%s=%#v: %v", tc.field.Name, tc.field.Value, err)
		}
	}
	if _, err := PlanProfileTemplate(domain.RequestObject{}); !errors.Is(err, ErrProfileTemplatePython) {
		t.Error("missing keys accepted")
	}
}
