package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestParseCourseSubscriptionWriteSplitsFormJSON pins the form JSON reading: a
// text Python parses but that keeps a lone surrogate is a valid value (its jsonb
// write fails at the row save), while the maximum nesting is recorded per field
// for the callers' measured recursion bands (X15).
func TestParseCourseSubscriptionWriteSplitsFormJSON(t *testing.T) {
	form := func(fields ...domain.RequestField) domain.RequestObject { return domain.RequestObject(fields) }
	deep := func(depth int) string { return strings.Repeat("[", depth) + strings.Repeat("]", depth) }
	for _, test := range []struct {
		name      string
		body      domain.RequestObject
		depth     int
		surrogate bool
		invalid   string // the field answering "Value must be valid JSON."
		stored    string // PythonJSONDumps of the parsed value
	}{
		{
			name: "quote-lone-surrogate", body: form(domain.RequestField{Name: "multiple_quote", Value: `["\ud800"]`}),
			depth: 1, surrogate: true, stored: `["\ud800"]`,
		},
		{
			name: "events-lone-surrogate", body: form(domain.RequestField{Name: "events", Value: `[{"id": 0, "note": "\ud800"}]`}),
			depth: 2, surrogate: true, stored: `[{"id": 0, "note": "\ud800"}]`,
		},
		{
			name: "quote-surrogate-pair", body: form(domain.RequestField{Name: "multiple_quote", Value: `["\ud83d\ude00"]`}),
			depth: 1, stored: `["\ud83d\ude00"]`,
		},
		{
			name: "quote-nested-501-served", body: form(domain.RequestField{Name: "multiple_quote", Value: deep(501)}),
			depth: 501, stored: deep(501),
		},
		{
			name: "quote-nested-600-served", body: form(domain.RequestField{Name: "multiple_quote", Value: deep(600)}),
			depth: 600,
		},
		{
			name: "quote-nested-9934-update-band", body: form(domain.RequestField{Name: "multiple_quote", Value: deep(9934)}),
			depth: 9934,
		},
		{
			name: "events-nested-9941-update-band", body: form(domain.RequestField{Name: "events", Value: deep(9941)}),
			depth: 9941,
		},
		{
			name: "quote-nested-10000-served", body: form(domain.RequestField{Name: "multiple_quote", Value: deep(10000)}),
			depth: 10000,
		},
		{
			name: "quote-invalid", body: form(domain.RequestField{Name: "multiple_quote", Value: "nope"}), invalid: "multiple_quote",
		},
	} {
		write, errs := ParseCourseSubscriptionWrite(test.body, true)
		field := "multiple_quote"
		if _, present := test.body.Lookup("events"); present {
			field = "events"
		}
		if write.FormJSONDepth[field] != test.depth || write.FormJSONSurrogate != test.surrogate {
			t.Errorf("%s: depth=%v surrogate=%v, want depth=%v surrogate=%v", test.name, write.FormJSONDepth[field],
				write.FormJSONSurrogate, test.depth, test.surrogate)
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
		if test.stored != "" {
			value := write.Quote
			if field == "events" {
				value = write.Events
			}
			if got := string(PythonJSONDumps(value)); got != test.stored {
				t.Errorf("%s: parsed %s, want %s", test.name, got, test.stored)
			}
		}
	}
}
