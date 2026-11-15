package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestRunnerJSONValuesKeepsALoneSurrogate is json.loads keeping the code
// point; DRF's renderer then refuses the value.
func TestRunnerJSONValuesKeepsALoneSurrogate(t *testing.T) {
	value, decoded, deep := RunnerJSONValues([]byte(`{"a": "\ud800", "b": [1, NaN]}`))
	if !decoded || deep {
		t.Fatalf("decoded=%v deep=%v", decoded, deep)
	}
	if RenderableJSON(value) {
		t.Fatal("a lone surrogate and a NaN do not render")
	}
	body, ok := value.(interface{ Lookup(string) (any, bool) })
	if !ok {
		t.Fatalf("value %#v is not an object", value)
	}
	text, found := body.Lookup("a")
	if !found || text != "\xed\xa0\x80" {
		t.Fatalf("a=%q found=%v", text, found)
	}
}

// TestRunnerJSONValuesRendersPlainReplies keeps ordinary bodies renderable.
func TestRunnerJSONValuesRendersPlainReplies(t *testing.T) {
	value, decoded, deep := RunnerJSONValues([]byte(`{"a": "x"}`))
	if !decoded || deep {
		t.Fatalf("decoded=%v deep=%v", decoded, deep)
	}
	if !RenderableJSON(value) {
		t.Fatal("a plain dict renders")
	}
}

// TestRunnerJSONValuesKeyHoldingASurrogate is a key the encoder cannot write
// either.
func TestRunnerJSONValuesKeyHoldingASurrogate(t *testing.T) {
	value, decoded, deep := RunnerJSONValues([]byte(`{"\udfff": 1}`))
	if !decoded || deep {
		t.Fatalf("decoded=%v deep=%v", decoded, deep)
	}
	object, ok := value.(domain.RequestObject)
	if !ok || len(object) != 1 || object[0].Name != "\xed\xbf\xbf" {
		t.Fatalf("keys %#v", value)
	}
	if RenderableJSON(value) {
		t.Fatal("a lone surrogate key does not render")
	}
}

// TestRunnerJSONValuesDeepNesting reports the decoder's limit.
func TestRunnerJSONValuesDeepNesting(t *testing.T) {
	deep := strings.Repeat("[", 501) + strings.Repeat("]", 501)
	if value, decoded, nested := RunnerJSONValues([]byte(deep)); value != nil || decoded || !nested {
		t.Fatalf("501 levels: value=%v decoded=%v deep=%v", value, decoded, nested)
	}
	shallow := strings.Repeat("[", 500) + strings.Repeat("]", 500)
	if _, decoded, nested := RunnerJSONValues([]byte(shallow)); !decoded || nested {
		t.Fatalf("500 levels: decoded=%v deep=%v", decoded, nested)
	}
}

// TestRunnerJSONValuesInvalidJSON is the ValueError call_runner treats as an
// unavailable runner.
func TestRunnerJSONValuesInvalidJSON(t *testing.T) {
	if value, decoded, deep := RunnerJSONValues([]byte(`{"a":`)); value != nil || decoded || deep {
		t.Fatalf("value=%v decoded=%v deep=%v", value, decoded, deep)
	}
}

// TestRunnerJSONValuesCodeUnitBytes keeps the three bytes of a code unit
// written as CESU-8 (the utf-8 codec with surrogatepass).
func TestRunnerJSONValuesCodeUnitBytes(t *testing.T) {
	raw := append(append([]byte(`{"a": "`), 0xed, 0xa0, 0x80), []byte(`"}`)...)
	value, decoded, deep := RunnerJSONValues(raw)
	if !decoded || deep {
		t.Fatalf("decoded=%v deep=%v", decoded, deep)
	}
	body, ok := value.(interface{ Lookup(string) (any, bool) })
	if !ok {
		t.Fatalf("value %#v is not an object", value)
	}
	text, found := body.Lookup("a")
	if !found || text != "\xed\xa0\x80" {
		t.Fatalf("a=%q found=%v", text, found)
	}
	if RenderableJSON(value) {
		t.Fatal("the code unit bytes do not render")
	}
}
