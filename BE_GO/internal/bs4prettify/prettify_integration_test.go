//go:build bs4integration

package bs4prettify

import (
	"encoding/json"
	"os"
	"testing"
)

// TestPrettifyAgainstBS4 checks the corpus written by
// TESTS/adapters/django/bs4_prettify_corpus.py: every subset input must be
// accepted with bs4's exact output; other markup is refused or matches.
func TestPrettifyAgainstBS4(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_BS4_CORPUS"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []goldenCase
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 10000 {
		t.Fatal("corpus", len(cases), err)
	}
	subset := 0
	for _, c := range cases {
		if c.Subset {
			subset++
		}
	}
	accepted := checkGolden(t, cases)
	t.Logf("%d cases, %d subset, %d accepted", len(cases), subset, accepted)
}
