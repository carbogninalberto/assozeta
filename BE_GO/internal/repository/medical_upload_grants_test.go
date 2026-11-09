package repository

import (
	"encoding/hex"
	"testing"
)

// pickle.dumps('a1000000-0000-4000-8000-000000701001', pickle.HIGHEST_PROTOCOL) on CPython 3.13.16.
func TestPickleShortStringMatchesCPython(t *testing.T) {
	const expected = "80059528000000000000008c2461313030303030302d303030302d343030302d383030302d303030303030373031303031942e"
	if got := hex.EncodeToString(pickleShortString("a1000000-0000-4000-8000-000000701001")); got != expected {
		t.Fatalf("pickle %s", got)
	}
	if association, err := decodeMedicalUpload(pickleShortString("a1000000-0000-4000-8000-000000701001")); err != nil ||
		association != "a1000000-0000-4000-8000-000000701001" {
		t.Fatalf("decode %q %v", association, err)
	}
}
