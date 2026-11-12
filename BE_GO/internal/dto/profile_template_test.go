package dto

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"strconv"
	"strings"
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
		{"12,5", 0, ErrProfileTemplatePython}, {"", 0, ErrProfileTemplatePython}, {" 5", 5, nil}, {"1__0", 0, ErrProfileTemplatePython},
		{"1_0", 10, nil}, {json.Number("1e400"), math.Inf(1), nil}, {"1e400", math.Inf(1), nil}, {"\u0661\u0662", 12, nil},
		{json.Number("1234567890123456"), 1234567890123456, nil}, {json.Number("1" + strings.Repeat("0", 400)), 0, ErrProfileTemplatePython},
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

// The input of profile-template-legacy-html-regulation and the value the
// generator stored (bs4 4.13.3 prettify()).
const legacyRegulation = `<!-- saved by an older editor --><!DOCTYPE html><![CDATA[legacy & data]]><?xml-stylesheet type="text/xsl" href="style.xsl"?><![if gte mso 9]><p>Word document</p><![endif]><P CLASS=legacy data-role=note hidden>Regolamento&nbsp del socio</P><p class="a" class="b" data-x=1>Quota &#150; annuale & socio &foo; &copy</p><a href="https://example.test/x?a=1&copy=1" title>statuto</a><p>a < b</p><p>senza chiusura<ul><li>uno<li>due</ul><b><i>x</b>y</i><br></br><table><tr><td>cella<td>altra</table><o:p>paragrafo office</o:p><my-widget data-kind="legacy">widget</my-widget>`

const legacyRegulationStored = "<!-- saved by an older editor -->\n<!DOCTYPE html>\n<![CDATA[legacy & data]]>\n<?xml-stylesheet type" +
	"=\"text/xsl\" href=\"style.xsl\"?>\n<!--[if gte mso 9]-->\n<p>\n Word document\n</p>\n<!--[endif]-->" +
	"\n<p class=\"legacy\" data-role=\"note\" hidden=\"\">\n Regolamento\u00a0 del socio\n</p>\n<p class=" +
	"\"b\" data-x=\"1\">\n Quota – annuale &amp; socio &amp;foo ©\n</p>\n<a href=\"https://example.test/x" +
	"?a=1&amp;copy=1\" title=\"\">\n statuto\n</a>\n<p>\n a &lt; b\n</p>\n<p>\n senza chiusura\n <ul>\n  " +
	"<li>\n   uno\n   <li>\n    due\n   </li>\n  </li>\n </ul>\n <b>\n  <i>\n   x\n  </i>\n </b>\n y\n <b" +
	"r/>\n <table>\n  <tr>\n   <td>\n    cella\n    <td>\n     altra\n    </td>\n   </td>\n  </tr>\n </ta" +
	"ble>\n <o:p>\n  paragrafo office\n </o:p>\n <my-widget data-kind=\"legacy\">\n  widget\n </my-widget" +
	">\n</p>\n"

// The input of profile-template-legacy-html-demand and its stored value.
const legacyDemand = `<script>if (a < b && c > d) { document.write("</p>"); }</script><style>p > a { content: "<&>"; } /* </p> */</style><textarea>Testo &amp; <b>grassetto</b> &lt;test&gt;</textarea><title>Domanda &amp; <i>socio</i> &copy;</title><pre>  prima riga
    seconda riga
	tabulata</pre><meta charset="iso-8859-1"><meta http-equiv="Content-Type" content="text/html; charset=latin1"><p>Il sottoscritto chiede di essere ammesso come <em>socio</em>.</p>`

const legacyDemandStored = "<script>\n if (a < b && c > d) { document.write(\"</p>\"); }\n</script>\n<style>\n p > a { content: " +
	"\"<&>\"; } /* </p> */\n</style>\n<textarea>Testo &amp; &lt;b&gt;grassetto&lt;/b&gt; &lt;test&gt;</te" +
	"xtarea>\n<title>\n Domanda &amp; &lt;i&gt;socio&lt;/i&gt; ©\n</title>\n<pre>  prima riga\n    second" +
	"a riga\n\ttabulata</pre>\n<meta charset=\"utf-8\"/>\n<meta content=\"text/html; charset=utf-8\" http" +
	"-equiv=\"Content-Type\"/>\n<p>\n Il sottoscritto chiede di essere ammesso come\n <em>\n  socio\n </e" +
	"m>\n .\n</p>\n"

// Documents of any shape follow bs4's html.parser recovery; the inputs bs4 (or
// the audit entry after it) raises on are ErrProfileTemplatePython.
func TestProfileTemplateLegacyHTML(t *testing.T) {
	for _, tc := range []struct {
		name, markup, want string
	}{
		{"legacy-html-regulation", legacyRegulation, legacyRegulationStored},
		{"legacy-html-demand", legacyDemand, legacyDemandStored},
	} {
		got, err := profileTemplatePrettify(tc.markup, "regulation")
		if err != nil || got != tc.want {
			t.Errorf("%s: %q, %v", tc.name, got, err)
		}
	}
	for _, markup := range []string{
		"<p>&#" + strings.Repeat("1", 5000) + ";</p>",
		strings.Repeat("x", 200) + "\xed\xa0\x80",
	} {
		if _, err := profileTemplatePrettify(markup, "regulation"); !errors.Is(err, ErrProfileTemplatePython) {
			t.Errorf("%q: %v", markup, err)
		}
	}
}

// TestProfileTemplateTextLimits covers _prettify_template_text: the length
// check counts code points, the nesting check runs after the parse, and a
// rejected text carries the field and message the view returns as its 400.
func TestProfileTemplateTextLimits(t *testing.T) {
	const (
		longMessage = "Il testo è troppo lungo (massimo 500000 caratteri)."
		deepMessage = "Il testo contiene troppi livelli annidati (massimo 32)."
	)
	rejected := func(t *testing.T, err error) *ProfileTemplateTextRejected {
		t.Helper()
		var rejection *ProfileTemplateTextRejected
		if !errors.As(err, &rejection) {
			t.Fatalf("%v is not a rejection", err)
		}
		return rejection
	}
	if _, err := profileTemplatePrettify(strings.Repeat("a", 500000), "regulation"); err != nil {
		t.Fatalf("500000 characters: %v", err)
	}
	// A non-ASCII letter counts as one code point, not three bytes.
	if _, err := profileTemplatePrettify(strings.Repeat("è", 500000), "regulation"); err != nil {
		t.Fatalf("500000 non-ASCII characters: %v", err)
	}
	// An astral code point is one character too, not a surrogate pair.
	if _, err := profileTemplatePrettify(strings.Repeat("😀", 500000), "regulation"); err != nil {
		t.Fatalf("500000 astral characters: %v", err)
	}
	for _, tc := range []struct {
		value any
		field string
	}{
		{strings.Repeat("a", 500001), "demand"},
		{strings.Repeat("è", 500001), "demand"},
		{strings.Repeat("😀", 500001), "demand"},
	} {
		rejection := rejected(t, profileTemplatePrettifyError(t, tc.value, tc.field))
		if rejection.Field != tc.field || rejection.Message != longMessage {
			t.Fatalf("%s: %#v", tc.field, rejection)
		}
	}
	accepted := strings.Repeat("<b>", 32) + "x"
	if _, err := profileTemplatePrettify(accepted, "regulation"); err != nil {
		t.Fatalf("depth 32: %v", err)
	}
	rejection := rejected(t, profileTemplatePrettifyError(t, strings.Repeat("<b>", 33)+"x", "regulation"))
	if rejection.Field != "regulation" || rejection.Message != deepMessage {
		t.Fatalf("depth 33: %#v", rejection)
	}
	// bs4 raises while parsing, before the tree is deep enough to be checked.
	deepRaise := strings.Repeat("<b>", 40) + "<p>&#" + strings.Repeat("1", 5000) + ";</p>"
	if _, err := profileTemplatePrettify(deepRaise, "regulation"); !errors.Is(err, ErrProfileTemplatePython) {
		t.Fatalf("bs4 raise behind deep nesting: %v", err)
	}
	if _, err := profileTemplatePrettify(json.Number("5"), "regulation"); !errors.Is(err, ErrProfileTemplatePython) {
		t.Fatalf("non-string: %v", err)
	}
	// Sections are prettified in list order, then regulation, then demand.
	section := func(text any) []any {
		return []any{domain.RequestObject{{Name: "name", Value: "A"}, {Name: "text", Value: text}}}
	}
	for _, tc := range []struct {
		name       string
		sections   any
		regulation any
		field      string
		message    string
	}{
		{"section-depth-before-regulation-length", section(strings.Repeat("<b>", 33) + "x"),
			strings.Repeat("a", 500001), "additional_sections", deepMessage},
		{"section-length-before-regulation-depth", section(strings.Repeat("a", 500001)),
			strings.Repeat("<b>", 33) + "x", "additional_sections", longMessage},
		{"regulation-before-demand", nil, strings.Repeat("<b>", 33) + "x", "regulation", deepMessage},
	} {
		changes := []domain.RequestField{{Name: "regulation", Value: tc.regulation}}
		if tc.sections != nil {
			changes = append(changes, domain.RequestField{Name: "additional_sections", Value: tc.sections})
		}
		_, err := PlanProfileTemplate(profileTemplateTestPayload(changes...))
		rejection := rejected(t, err)
		if rejection.Field != tc.field || rejection.Message != tc.message {
			t.Errorf("%s: %#v", tc.name, rejection)
		}
	}
}

// TestPlanProfileTemplateKeyOrder pins where each initial_data key is read: a
// missing key raises at the access, so a text rejected before it still answers
// the 400 while one behind it answers the 500.
func TestPlanProfileTemplateKeyOrder(t *testing.T) {
	deep := strings.Repeat("<b>", 33) + "x"
	section := []any{domain.RequestObject{{Name: "name", Value: "A"}, {Name: "text", Value: deep}}}
	payload := func(missing string, changes ...domain.RequestField) domain.RequestObject {
		data := domain.RequestObject{}
		for _, key := range profileTemplateKeys {
			if key != missing {
				data = append(data, domain.RequestField{Name: key})
			}
		}
		for _, change := range changes {
			data = PythonSet(data, change.Name, change.Value)
		}
		return data
	}
	for _, tc := range []struct {
		name    string
		missing string
		change  domain.RequestField
		field   string
	}{
		// additional_sections, the loop and regulation are read before demand.
		{"section-depth-before-missing-demand", "demand",
			domain.RequestField{Name: "additional_sections", Value: section}, "additional_sections"},
		{"regulation-depth-before-missing-demand", "demand",
			domain.RequestField{Name: "regulation", Value: deep}, "regulation"},
		{"section-depth-before-missing-regulation", "regulation",
			domain.RequestField{Name: "additional_sections", Value: section}, "additional_sections"},
	} {
		_, err := PlanProfileTemplate(payload(tc.missing, tc.change))
		var rejection *ProfileTemplateTextRejected
		if !errors.As(err, &rejection) {
			t.Fatalf("%s: %v is not a rejection", tc.name, err)
		}
		if rejection.Field != tc.field {
			t.Errorf("%s: %#v", tc.name, rejection)
		}
	}
	for _, tc := range []struct {
		name    string
		missing string
		change  domain.RequestField
	}{
		// demand is read after regulation, and the loop after additional_sections.
		{"demand-depth-after-missing-regulation", "regulation", domain.RequestField{Name: "demand", Value: deep}},
		{"regulation-depth-after-missing-additional-sections", "additional_sections",
			domain.RequestField{Name: "regulation", Value: deep}},
	} {
		if _, err := PlanProfileTemplate(payload(tc.missing, tc.change)); !errors.Is(err, ErrProfileTemplatePython) {
			t.Errorf("%s: %v", tc.name, err)
		}
	}
}

func profileTemplatePrettifyError(t *testing.T, value any, field string) error {
	t.Helper()
	_, err := profileTemplatePrettify(value, field)
	return err
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
		{domain.RequestField{Name: "regulation", Value: "<!-- c -->"}, nil},
		{domain.RequestField{Name: "subscription_fee_plans", Value: []any{"x"}}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "membership_fee_plans", Value: []any{domain.RequestObject{{Name: "membership_fee", Value: json.Number("1")}}}},
			ErrProfileTemplatePython},
		{domain.RequestField{Name: "logo", Value: json.Number("5")}, nil},
		{domain.RequestField{Name: "invoice_template", Value: []any{}}, nil},
		{domain.RequestField{Name: "enabled_for", Value: []any{"\xed\xa0\x80"}}, nil},
		{domain.RequestField{Name: "invoice_template", Value: "\xed\xa0\x80"}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "logo", Value: "a,\xed\xa0\x80"}, ErrProfileTemplatePython},
		{domain.RequestField{Name: "logo", Value: "\xed\xa0\x80,AA"}, ErrProfileTemplateNotPorted},
	} {
		if _, err := PlanProfileTemplate(profileTemplateTestPayload(tc.field)); !errors.Is(err, tc.err) {
			t.Errorf("%s=%#v: %v", tc.field.Name, tc.field.Value, err)
		}
	}
	commentPlan, err := PlanProfileTemplate(profileTemplateTestPayload(
		domain.RequestField{Name: "regulation", Value: "<!-- c -->"}))
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range commentPlan.Fields {
		if field.Name == "regulation" && field.Value != "<!-- c -->\n" {
			t.Errorf("regulation comment: %#v", field.Value)
		}
	}
	if _, err := PlanProfileTemplate(domain.RequestObject{}); !errors.Is(err, ErrProfileTemplatePython) {
		t.Error("missing keys accepted")
	}
}
