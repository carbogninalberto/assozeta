package refusal

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

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
