package service

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSetupTokenMatchesDjangoHeaderDecoding(t *testing.T) {
	cases := []struct {
		provided, expected string
		want               bool
	}{
		{"contract-setup-token", "contract-setup-token", true},
		{"contract-setup-toke", "contract-setup-token", false},
		{"", "", false},
		{"anything", "", false},
		// Django decodes header bytes as latin-1 before comparing UTF-8 encodings.
		{"\xc3\xa8", "è", false},
	}
	for _, c := range cases {
		if got := setupTokenMatches(c.provided, c.expected); got != c.want {
			t.Errorf("setupTokenMatches(%q, %q) = %v", c.provided, c.expected, got)
		}
	}
}

func TestPythonRound2(t *testing.T) {
	for value, want := range map[float64]float64{0.0009: 0, 0.005: 0.01, 0.015: 0.01, 1.125: 1.12, 2.675: 2.67} {
		if got := pythonRound2(value); got != want {
			t.Errorf("round(%v, 2) = %v, want %v", value, got, want)
		}
	}
}

func TestValidateArchiveRejectsNonZip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "backup.zip")
	if err := os.WriteFile(path, []byte("not a zip"), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := (&AssociationTransfer{}).validateArchive(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if result.valid || len(result.errors) != 1 || result.errors[0] != "Not a valid ZIP file: "+path || len(result.info) != 0 {
		t.Fatalf("unexpected result %+v", result)
	}
}

func TestPythonMkdtempName(t *testing.T) {
	dir, err := pythonMkdtemp()
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	name := filepath.Base(dir)
	if !strings.HasPrefix(name, "tmp") || len(name) != 11 || strings.Trim(name[3:], tempNameCharacters) != "" {
		t.Fatalf("unexpected temporary directory %q", name)
	}
}
