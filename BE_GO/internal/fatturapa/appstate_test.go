package fatturapa

import (
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Text that is not JSON (a jsonb column always holds JSON), and an integer json.loads refuses to read,
// are application-state errors: Django's 500 page, never a handoff.
func TestUnexpectedJSONIsAppState(t *testing.T) {
	for name, text := range map[string]string{
		"not JSON":     `[`,
		"4301 digits":  `[` + strings.Repeat("9", 4301) + `]`,
		"-4301 digits": `[-` + strings.Repeat("9", 4301) + `]`,
	} {
		_, err := parseJSON(text)
		if !errors.Is(err, ErrUnexpectedJSON) || !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	if _, err := parseJSON(`[-` + strings.Repeat("9", 4300) + `]`); err != nil {
		t.Errorf("4300 digits: %v", err)
	}
}
