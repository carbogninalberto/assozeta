package dto

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// instructorHoursDeepArrays is a JSON array nested depth levels: auditJSONDepth
// of the text and JSONValueDepth of the parsed value are both depth.
func instructorHoursDeepArrays(depth int) string {
	return strings.Repeat("[", depth) + strings.Repeat("]", depth)
}

// instructorHoursFormWrite parses the given name/value pairs as an HTML form
// update (partial), the path the measured JSON bands belong to.
func instructorHoursFormWrite(body domain.RequestObject) (InstructorHoursWrite, map[string]any, error) {
	return ParseInstructorHoursWrite(body, true, true)
}

// The form JSON fields record the text depth the scanner reached and the parsed
// value's depth; the service answers the save-time dump band from the value
// depth and the parse answers the text band itself (owner decision X15).
func TestInstructorHoursFormJSONDepth(t *testing.T) {
	for _, depth := range []int{1, 501, 9938, 9939, 9941} {
		for _, name := range []string{"courses", "calculation_data"} {
			write, errs, err := instructorHoursFormWrite(domain.RequestObject{{Name: name, Value: instructorHoursDeepArrays(depth)}})
			if err != nil || errs != nil {
				t.Fatalf("%s depth %d: errs=%v err=%v", name, depth, errs, err)
			}
			if write.JSONTextDepth[name] != depth || write.JSONValueDepth[name] != depth {
				t.Fatalf("%s depth %d: text=%d value=%d", name, depth, write.JSONTextDepth[name], write.JSONValueDepth[name])
			}
			if JSONValueDepth(write.JSON[name]) != depth || write.NonFinite {
				t.Fatalf("%s depth %d: value=%#v nonFinite=%v", name, depth, write.JSON[name], write.NonFinite)
			}
		}
	}
	// From the measured text band DRF's json.loads raises RecursionError: the
	// 500 page before any write, whatever the other fields hold.
	for _, depth := range []int{9942, 9960, formJSONNesting} {
		for _, name := range []string{"courses", "calculation_data"} {
			_, _, err := instructorHoursFormWrite(domain.RequestObject{{Name: name, Value: instructorHoursDeepArrays(depth)},
				{Name: "hours", Value: "abc"}})
			if !errors.Is(err, errInstructorHoursFormJSONTooDeep) || !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("%s depth %d: %v", name, depth, err)
			}
			if errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("%s depth %d is handed off: %v", name, depth, err)
			}
		}
	}
}

// A duplicate key overwrites the deep part: the parser keeps the last value (in
// the first position), so the text depth is past the save-time band while the
// value the row would dump is shallow.
func TestInstructorHoursFormJSONRepeatedKey(t *testing.T) {
	text := `{"a": ` + strings.Repeat(`{"a":`, 9900) + "1" + strings.Repeat("}", 9900) + `, "a": 1}`
	write, errs, err := instructorHoursFormWrite(domain.RequestObject{{Name: "courses", Value: text}})
	if err != nil || errs != nil {
		t.Fatalf("errs=%v err=%v", errs, err)
	}
	if write.JSONTextDepth["courses"] != 9901 || write.JSONValueDepth["courses"] != 1 {
		t.Fatalf("text=%d value=%d", write.JSONTextDepth["courses"], write.JSONValueDepth["courses"])
	}
	value, ok := write.JSON["courses"].(domain.RequestObject)
	if !ok || len(value) != 1 || value[0].Name != "a" || value[0].Value != json.Number("1") {
		t.Fatalf("value=%#v", write.JSON["courses"])
	}
	// The text depth still answers first when the overwritten text is past the
	// text band: Django's scanner raises before it reaches the second key.
	deep := `{"a": ` + strings.Repeat(`{"a":`, 9941) + "1" + strings.Repeat("}", 9941) + `, "a": 1}`
	if _, _, err := instructorHoursFormWrite(domain.RequestObject{{Name: "courses", Value: deep}}); !errors.Is(err, errInstructorHoursFormJSONTooDeep) {
		t.Fatalf("deep repeated key: %v", err)
	}
}

// NaN, Infinity, a number beyond the double range and a lone surrogate escape
// parse but the jsonb write cannot store them: the field is not set and the
// service answers the 500 page, as before.
func TestInstructorHoursFormJSONNonFinite(t *testing.T) {
	for _, text := range []string{`[NaN]`, `[Infinity]`, `{"a": 1e400}`, `["\ud800"]`} {
		write, errs, err := instructorHoursFormWrite(domain.RequestObject{{Name: "courses", Value: text}})
		if err != nil || errs != nil {
			t.Fatalf("%s: errs=%v err=%v", text, errs, err)
		}
		if !write.NonFinite {
			t.Fatalf("%s must be non-finite", text)
		}
		if _, set := write.JSON["courses"]; set {
			t.Fatalf("%s must not be set", text)
		}
	}
	// A surrogate pair is valid text and stays finite.
	write, errs, err := instructorHoursFormWrite(domain.RequestObject{{Name: "courses", Value: `["😀"]`}})
	if err != nil || errs != nil || write.NonFinite {
		t.Fatalf("surrogate pair: errs=%v err=%v nonFinite=%v", errs, err, write.NonFinite)
	}
}

// Invalid text is the field's "Value must be valid JSON."; a syntax error stops
// the scanner, so only the nesting before it can raise RecursionError.
func TestInstructorHoursFormJSONInvalid(t *testing.T) {
	for _, text := range []string{"nope", "", `[1] x`, "x" + instructorHoursDeepArrays(10000)} {
		write, errs, err := instructorHoursFormWrite(domain.RequestObject{{Name: "courses", Value: text}})
		if err != nil {
			t.Fatalf("%q: %v", text, err)
		}
		if write.NonFinite {
			t.Fatalf("%q must not be non-finite", text)
		}
		codes, _ := errs["courses"].([]string)
		if len(codes) != 1 || codes[0] != InstructorHoursJSON {
			t.Fatalf("%q: errs=%v", text, errs)
		}
	}
	// The document is read up to its syntax error: a deep tail after it is not
	// the 500 page.
	text := instructorHoursDeepArrays(9941) + "x" + instructorHoursDeepArrays(100)
	if _, _, err := instructorHoursFormWrite(domain.RequestObject{{Name: "courses", Value: text}}); err != nil {
		t.Fatalf("deep invalid text: %v", err)
	}
}

// A JSON body is already parsed: neither depth is measured.
func TestInstructorHoursJSONBodyIsNotMeasured(t *testing.T) {
	write, errs, err := ParseInstructorHoursWrite(domain.RequestObject{{Name: "courses", Value: []any{json.Number("1")}}}, false, true)
	if err != nil || errs != nil {
		t.Fatalf("errs=%v err=%v", errs, err)
	}
	if len(write.JSONTextDepth) != 0 || len(write.JSONValueDepth) != 0 {
		t.Fatalf("text=%v value=%v", write.JSONTextDepth, write.JSONValueDepth)
	}
	if JSONValueDepth(write.JSON["courses"]) != 1 {
		t.Fatalf("value=%#v", write.JSON["courses"])
	}
}

// An integer past Python's 4300-digit limit makes json.loads raise ValueError,
// which DRF's JSONField.to_internal_value turns into the field's 'invalid'.
func TestInstructorHoursFormJSONDigitLimitIsInvalid(t *testing.T) {
	body := domain.RequestObject{{Name: "courses", Value: "[" + strings.Repeat("7", 4301) + "]"}}
	write, errs, err := ParseInstructorHoursWrite(body, true, true)
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	if write.NonFinite {
		t.Fatalf("NonFinite set for a ValueError of json.loads")
	}
	codes, _ := errs["courses"].([]string)
	if len(codes) != 1 || codes[0] != InstructorHoursJSON {
		t.Fatalf("errs = %v, want courses %q", errs, InstructorHoursJSON)
	}
}
