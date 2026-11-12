package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestImageVersionPrecedesConfiguredEnvironment(t *testing.T) {
	path := filepath.Join(t.TempDir(), "VERSION")
	for _, tc := range []struct{ content, expected string }{
		{"v1.2.3\n", "v1.2.3"}, {"  1.2.4 \n", "1.2.4"}, {"\n", "configured"},
	} {
		if err := os.WriteFile(path, []byte(tc.content), 0600); err != nil {
			t.Fatal(err)
		}
		got, err := runningVersion(path, "configured")
		if err != nil || got != tc.expected {
			t.Fatalf("running version = %q, %v; want %q", got, err, tc.expected)
		}
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	got, err := runningVersion(path, "configured")
	if err != nil || got != "configured" {
		t.Fatalf("missing image metadata fallback = %q, %v", got, err)
	}
}

func TestUnreadableOrInvalidImageVersionCannotMasqueradeAsConfiguredRelease(t *testing.T) {
	root := t.TempDir()
	invalid := filepath.Join(root, "private-version-input")
	if err := os.WriteFile(invalid, []byte{0xff}, 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{root, invalid} {
		got, err := runningVersion(path, "configured")
		if err == nil || got != "" || strings.Contains(err.Error(), path) {
			t.Fatalf("invalid image metadata accepted or exposed: %q, %v", got, err)
		}
	}
}
