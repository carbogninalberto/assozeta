package repository

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// auditDeepJSON is a JSON array nested depth levels ('[]' is 1), the shape the
// payment routes' stored-JSON cases author.
func auditDeepJSON(depth int) string {
	return strings.Repeat("[", depth) + strings.Repeat("]", depth)
}

// A stored JSON value past the reference parser is rendered when the caller
// measured the depth auditlog's json.dumps of the row parses at (owner decision
// X15): payment/<uid>/update saves a row up to the jsonb adapter's edge, and its
// audit entry then renders the same value. Without a measured depth the
// reference limit still refuses it.
func TestRenderAuditValueStoredJSONDepth(t *testing.T) {
	const depth = 9929
	deep := auditDeepJSON(depth)
	row := []byte(`{"course": ` + deep + `}`)
	if _, err := renderAuditValue("jsonb", json.RawMessage(deep), row, "course", 0); !errors.Is(err, dto.ErrReferenceNesting) {
		t.Fatalf("reference limit: err=%v, want the nesting refusal", err)
	}
	rendered, err := renderAuditValue("jsonb", json.RawMessage(deep), row, "course", paymentAuditJSONDepth)
	if err != nil {
		t.Fatalf("measured depth: err=%v", err)
	}
	if rendered != deep {
		t.Fatalf("measured depth: rendered %d bytes, want the %d byte sorted dumps", len(rendered), len(deep))
	}
	// One level past the measured band is what the audit's own dumps cannot read.
	if _, err := renderAuditValue("jsonb", json.RawMessage(deep), row, "course", depth-1); !errors.Is(err, dto.ErrReferenceNesting) {
		t.Fatalf("past the measured depth: err=%v, want the nesting refusal", err)
	}
}
