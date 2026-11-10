package medicalform

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

// The golden values and digests are fill_pdf_fields' own output (generate_medical_form_skeleton.py).
func TestFillBiellaMatchesPypdf(t *testing.T) {
	raw, err := os.ReadFile("testdata/golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Values map[string]string `json:"values"`
		Length int               `json:"length"`
		SHA256 string            `json:"sha256"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 5 {
		t.Fatalf("golden cases: %v (%d)", err, len(cases))
	}
	for index, item := range cases {
		filled := FillBiella(item.Values)
		sum := sha256.Sum256(filled)
		if len(filled) != item.Length || hex.EncodeToString(sum[:]) != item.SHA256 {
			t.Errorf("case %d: %d bytes %x, want %d bytes %s", index, len(filled), sum, item.Length, item.SHA256)
		}
	}
}

func TestBiellaKeys(t *testing.T) {
	keys := BiellaKeys()
	found := false
	for _, key := range keys {
		found = found || key == SignatureKey
	}
	if len(keys) != 26 || !found {
		t.Fatalf("keys: %v", keys)
	}
}
