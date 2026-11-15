package refusal

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// An application-state error is not a refusal: it matches ErrAppState and only
// that, so internal/handoff never replays it to Django.
func TestAppStateIsNotARefusal(t *testing.T) {
	err := AppState("missing cache entry")
	if !errors.Is(err, ErrAppState) {
		t.Fatal("AppState must match ErrAppState")
	}
	if errors.Is(err, ErrNotPorted) {
		t.Fatal("AppState must not match ErrNotPorted")
	}
	if _, refused := Reason(err); refused {
		t.Fatal("AppState must not be a refusal")
	}
}

// A wrapped application-state error is still found, as Reason does for refusals.
func TestAppStateReasonFindsWrappedErrors(t *testing.T) {
	wrapped := fmt.Errorf("service: %w", AppState("invalid stored setting"))
	reason, ok := AppStateReason(wrapped)
	if !ok || reason != "invalid stored setting" {
		t.Fatalf("reason %q, ok %v", reason, ok)
	}
	if !errors.Is(wrapped, ErrAppState) {
		t.Fatal("a wrapped AppState must match ErrAppState")
	}
}

// A refusal sentinel created with New is a handoff refusal, never app state.
func TestNewIsNotAppState(t *testing.T) {
	err := New("test input not ported")
	if _, ok := AppStateReason(err); ok {
		t.Fatal("a refusal sentinel must not be an application-state error")
	}
	if errors.Is(err, ErrAppState) {
		t.Fatal("a refusal sentinel must not match ErrAppState")
	}
}

// Every "not ported" sentinel must be a refusal, or the handoff to Django
// (internal/handoff) would answer it with the 500 page.
func TestNotPortedSentinelsAreRefusals(t *testing.T) {
	definition := regexp.MustCompile(`(?m)^\s*(?:var\s+)?([Ee]rr\w*(?:NotPorted|Unported|Unsupported|PrimaryKeyChange|Refused|CacheShape)\w*)\s*=\s*errors\.New\(` +
		`|^\s*(?:var\s+)?([Ee]rr\w+)\s*=\s*errors\.New\("[^"\n]*not ported[^"\n]*"\)`)
	// Django-parity 500s and non-HTTP evaluators, not refusals.
	allowed := map[string]bool{"ErrNotPorted": true, "ErrStaffBoardUnsupportedBody": true, "ErrScheduleUnsupported": true}
	checked := 0
	err := filepath.WalkDir("..", func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		source, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		checked++
		for _, match := range definition.FindAllStringSubmatch(string(source), -1) {
			if name := match[1] + match[2]; !allowed[name] {
				t.Errorf("%s: %s uses errors.New; use refusal.New", path, name)
			}
		}
		return nil
	})
	if err != nil || checked < 100 {
		t.Fatalf("walked %d files: %v", checked, err)
	}
}
