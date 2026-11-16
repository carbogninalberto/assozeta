package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestParseCourseSubscriptionWriteSplitsFormJSON pins the split of the form
// JSON refusal: a text Python parses but that keeps a lone surrogate is a valid
// value (its jsonb write fails at the row save), while nesting past the ported
// depth stays a refusal.
func TestParseCourseSubscriptionWriteSplitsFormJSON(t *testing.T) {
	form := func(fields ...domain.RequestField) domain.RequestObject { return domain.RequestObject(fields) }
	deep := strings.Repeat("[", 501) + strings.Repeat("]", 501)
	for _, test := range []struct {
		name      string
		body      domain.RequestObject
		deep      bool
		surrogate bool
		invalid   string // the field answering "Value must be valid JSON."
		stored    string // PythonJSONDumps of the parsed value
	}{
		{
			name: "quote-lone-surrogate", body: form(domain.RequestField{Name: "multiple_quote", Value: `["\ud800"]`}),
			surrogate: true, stored: `["\ud800"]`,
		},
		{
			name: "events-lone-surrogate", body: form(domain.RequestField{Name: "events", Value: `[{"id": 0, "note": "\ud800"}]`}),
			surrogate: true, stored: `[{"id": 0, "note": "\ud800"}]`,
		},
		{
			name: "quote-surrogate-pair", body: form(domain.RequestField{Name: "multiple_quote", Value: `["\ud83d\ude00"]`}),
			stored: `["\ud83d\ude00"]`,
		},
		{
			name: "quote-deep", body: form(domain.RequestField{Name: "multiple_quote", Value: deep}), deep: true,
		},
		{
			name: "events-deep", body: form(domain.RequestField{Name: "events", Value: deep}), deep: true,
		},
		{
			name: "quote-invalid", body: form(domain.RequestField{Name: "multiple_quote", Value: "nope"}), invalid: "multiple_quote",
		},
	} {
		write, errs := ParseCourseSubscriptionWrite(test.body, true)
		if write.FormJSONDeep != test.deep || write.FormJSONSurrogate != test.surrogate {
			t.Errorf("%s: deep=%v surrogate=%v, want deep=%v surrogate=%v", test.name, write.FormJSONDeep,
				write.FormJSONSurrogate, test.deep, test.surrogate)
		}
		if test.invalid != "" {
			codes, ok := errs[test.invalid].([]string)
			if !ok || len(codes) != 1 || codes[0] != CourseSubscriptionJSONInvalid {
				t.Errorf("%s: errors %v, want %s for %s", test.name, errs, CourseSubscriptionJSONInvalid, test.invalid)
			}
			continue
		}
		if len(errs) > 0 {
			t.Errorf("%s: unexpected field errors %v", test.name, errs)
		}
		if !test.deep && test.stored != "" {
			value := write.Quote
			if _, present := test.body.Lookup("events"); present {
				value = write.Events
			}
			if got := string(PythonJSONDumps(value)); got != test.stored {
				t.Errorf("%s: parsed %s, want %s", test.name, got, test.stored)
			}
		}
	}
}
