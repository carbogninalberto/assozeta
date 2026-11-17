package dto

import (
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ParseStoredJSONDepth bounds a stored text by its total depth, the way the
// sites' measured bands count it (AuditJSONDepth: '[]' is 1): a document at
// maxDepth parses, one level deeper is refused, and json.loads' integer digit
// limit still applies.
func TestParseStoredJSONDepthBoundsTotalDepth(t *testing.T) {
	if AuditJSONDepth(`[]`) != 1 || AuditJSONDepth(`{"a": {"a": 1}}`) != 2 {
		t.Fatalf("depth: %d %d", AuditJSONDepth(`[]`), AuditJSONDepth(`{"a": {"a": 1}}`))
	}
	at := strings.Repeat("[", 3) + "0" + strings.Repeat("]", 3)
	over := strings.Repeat("[", 4) + "0" + strings.Repeat("]", 4)
	if AuditJSONDepth(at) != 3 || AuditJSONDepth(over) != 4 {
		t.Fatalf("authored depths: %d %d", AuditJSONDepth(at), AuditJSONDepth(over))
	}
	value, err := ParseStoredJSONDepth(at, 3)
	if err != nil {
		t.Fatalf("at the band: %v", err)
	}
	if array, ok := value.([]any); !ok || len(array) != 1 {
		t.Fatalf("at the band: %#v", value)
	}
	if _, err := ParseStoredJSONDepth(over, 3); !errors.Is(err, ErrReferenceNesting) {
		t.Fatalf("one level past the band: %v", err)
	}
	if _, err := ParseStoredJSONDepth("1"+strings.Repeat("0", 4300), 1); !errors.Is(err, ErrLoginRateJSONValue) {
		t.Fatalf("4301 digits: %v", err)
	}
	if _, err := ParseStoredJSONDepth("1"+strings.Repeat("0", 4299), 1); err != nil {
		t.Fatalf("4300 digits: %v", err)
	}
	if _, err := ParseStoredJSONDepth(`{"a":`, 3); err == nil {
		t.Fatal("malformed stored text accepted")
	}
}

// ParseStoredProfileTablesJSON is the profile-table reader (lone surrogate
// escapes kept as code units) with the same total-depth band: the payment
// routes render a stored value at the depth auditlog dumps it (owner decision
// X15).
func TestParseStoredProfileTablesJSONBoundsTotalDepth(t *testing.T) {
	at := `{"a": "` + `\ud800` + `"}`
	if AuditJSONDepth(at) != 1 {
		t.Fatalf("authored depth %d", AuditJSONDepth(at))
	}
	value, err := ParseStoredProfileTablesJSON([]byte(at), 1)
	if err != nil {
		t.Fatalf("at the band: %v", err)
	}
	object, ok := value.(domain.RequestObject)
	if !ok || len(object) != 1 || object[0].Name != "a" || object[0].Value != "\xed\xa0\x80" {
		t.Fatalf("at the band: %#v", value)
	}
	if _, err := ParseStoredProfileTablesJSON([]byte(at), 0); !errors.Is(err, ErrReferenceNesting) {
		t.Fatalf("one level past the band: %v", err)
	}
	deep := strings.Repeat("[", referenceJSONNesting+1) + strings.Repeat("]", referenceJSONNesting+1)
	if _, err := ParseStoredProfileTablesJSON([]byte(deep), referenceJSONNesting); !errors.Is(err, ErrReferenceNesting) {
		t.Fatalf("past the reference limit: %v", err)
	}
	if _, err := ParseStoredProfileTablesJSON([]byte(deep), AuditJSONDepth(deep)); err != nil {
		t.Fatalf("at the measured depth: %v", err)
	}
}
