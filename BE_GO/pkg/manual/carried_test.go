package manual

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCarriedPathlibParent(t *testing.T) {
	for path, want := range map[string]string{
		"": ".", ".": ".", "a": ".", "a/": ".", "./a": ".", "a/./b": "a", "a//b/": "a",
		"/": "/", "/a": "/", "/a/b/": "/a", "//a/b": "//a", "///a/b": "/a", "..": ".", "a/..": "a", "../a": "..",
	} {
		if got := pathlibParent(path); got != want {
			t.Errorf("pathlibParent(%q) = %q, want %q", path, got, want)
		}
	}
}

func TestCarriedOriginMarker(t *testing.T) {
	revision := strings.Repeat("e", 40)
	for _, tc := range []struct {
		name, marker, release string
		carried, fails        bool
	}{
		{"absent", "", "", false, false},
		{"blank", " \n\t\x1c", "", false, false},
		{"valid", "release-origin " + revision + "\n", "release-origin", true, false},
		{"unit-separator", "release-origin\x1f" + revision, "release-origin", true, false},
		{"ideographic-space", "release-origin　" + revision, "release-origin", true, false},
		{"one-field", "release-origin", "", false, true},
		{"three-fields", "a " + revision + " c", "", false, true},
		{"uppercase", "release-origin " + strings.ToUpper(revision), "", false, true},
		{"invalid-utf8", "\xff " + revision, "", false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			base := t.TempDir()
			bundle := filepath.Join(base, "release-manual")
			if tc.name != "absent" {
				if err := os.WriteFile(filepath.Join(base, "MANUAL_CARRIED_FROM"), []byte(tc.marker), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			release, gotRevision, carried, err := carriedOrigin(bundle + "/")
			if (err != nil) != tc.fails || carried != tc.carried || release != tc.release {
				t.Fatalf("release=%q carried=%v err=%v", release, carried, err)
			}
			if carried && gotRevision != revision {
				t.Fatalf("revision=%q", gotRevision)
			}
		})
	}
	if os.Geteuid() != 0 {
		// pathlib reads raise PermissionError: an unreadable marker fails closed.
		base := t.TempDir()
		marker := filepath.Join(base, "MANUAL_CARRIED_FROM")
		if err := os.WriteFile(marker, []byte("release-origin "+revision), 0); err != nil {
			t.Fatal(err)
		}
		if _, _, carried, err := carriedOrigin(filepath.Join(base, "release-manual")); carried || err == nil {
			t.Fatal("an unreadable marker must fail", carried, err)
		}
	}
	directory := t.TempDir()
	if err := os.Mkdir(filepath.Join(directory, "MANUAL_CARRIED_FROM"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, _, carried, err := carriedOrigin(filepath.Join(directory, "release-manual")); carried || err != nil {
		t.Fatal("a directory marker is not a carried origin", carried, err)
	}
}
