package documentkey

import (
	"errors"
	"testing"
)

func TestSaveName(t *testing.T) {
	for name, want := range map[string]string{"documenti/2026/ricevuta n.1.pdf": "documenti/2026/ricevuta n.1.pdf",
		"./archivio//export.tar.gz": "archivio/export.tar.gz", "a/./b.pdf": "a/b.pdf"} {
		got, err := SaveName(name)
		if err != nil || got != want {
			t.Fatalf("%q: %q %v", name, got, err)
		}
	}
	for name, message := range map[string]string{"documenti/../../fuori.pdf": "Detected path traversal attempt in 'documenti/../../fuori.pdf'",
		"/abs.pdf": "Detected path traversal attempt in '/abs.pdf'", "dir/": "Could not derive file name from 'dir/'",
		"a/..": "Could not derive file name from 'a/..'"} {
		_, err := SaveName(name)
		var suspicious *SuspiciousFileError
		if !errors.As(err, &suspicious) || suspicious.Message != message {
			t.Fatalf("%q: %v", name, err)
		}
	}
	for _, name := range []string{`a\b.pdf`, "a\x00b.pdf", string([]byte{0xff})} {
		if _, err := SaveName(name); !errors.Is(err, ErrStoredName) {
			t.Fatalf("%q: %v", name, err)
		}
	}
}
