//go:build documentkeyintegration

package documentkey

import (
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestMedicalS3KeyDjango(t *testing.T) {
	var fixture struct {
		Cases []struct {
			Created                          time.Time
			ID, Filename, Location, Expected string
			Reject                           bool
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_DOCUMENT_KEY_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/document_keys.py:", err)
	}
	if err := json.Unmarshal(raw, &fixture); err != nil || len(fixture.Cases) < 5000 {
		t.Fatal("missing document key cases", err)
	}
	for i, row := range fixture.Cases {
		key, err := MedicalS3Key(row.Created, uuid.MustParse(row.ID), row.Filename, row.Location)
		if row.Reject {
			if key != "" || err != ErrMedicalKey {
				t.Fatalf("case %d allowed unsafe key %q: %v", i, key, err)
			}
		} else if err != nil || key != row.Expected {
			t.Fatalf("case %d (%s): %q, %v; Django %q", i, row.Created.Format(time.RFC3339Nano), key, err, row.Expected)
		}
	}
}
