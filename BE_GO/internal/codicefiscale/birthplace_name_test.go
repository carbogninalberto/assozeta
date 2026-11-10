package codicefiscale

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"
)

// TestDecodedBirthplaceName requires the helper's birthplace name (or None)
// recorded by TESTS/adapters/go/codicefiscale_decode_golden.py.
func TestDecodedBirthplaceName(t *testing.T) {
	raw, err := os.ReadFile("testdata/decode_names.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Year  int `json:"year"`
		Cases []struct {
			Code string  `json:"code"`
			Name *string `json:"name"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 1000 {
		t.Fatalf("only %d cases", len(golden.Cases))
	}
	now := time.Date(golden.Year, 6, 1, 0, 0, 0, 0, time.UTC)
	named, refused := 0, 0
	for _, item := range golden.Cases {
		name, found, err := DecodedBirthplaceName(item.Code, now)
		if errors.Is(err, ErrUnsupported) {
			refused++
			continue
		}
		if err != nil {
			t.Fatalf("%q: %v", item.Code, err)
		}
		switch {
		case item.Name == nil && found:
			t.Errorf("%q: %q, want none", item.Code, name)
		case item.Name != nil && (!found || name != *item.Name):
			t.Errorf("%q: %q (%v), want %q", item.Code, name, found, *item.Name)
		}
		if found {
			named++
		}
	}
	if named < 500 || refused > 5 {
		t.Fatalf("%d named, %d refused", named, refused)
	}
}
