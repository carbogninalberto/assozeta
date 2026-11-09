package service

import (
	"encoding/json"
	"errors"
	"testing"
)

// Goldens from CPython 3.13: instance.restore.archive.format_size, the
// UUIDField/IntegerField/BooleanField/DateField to_python outcomes the
// archive validation relies on.
func TestRestoreFormatSize(t *testing.T) {
	for size, want := range map[int64]string{512: "512 byte", 1536: "1.50 KiB", 5 << 30: "5.00 GiB", 536870912: "512.00 MiB",
		1023: "1023 byte", 1025: "1.00 KiB", 20 << 30: "20.00 GiB", 1<<20 + 5243: "1.01 MiB"} {
		if got := restoreFormatSize(size); got != want {
			t.Errorf("format_size(%d) = %q, want %q", size, got, want)
		}
	}
}

func TestRestoreUUIDText(t *testing.T) {
	ok := map[string]string{
		"12345678-1234-5678-1234-567812345678":          "12345678-1234-5678-1234-567812345678",
		"{12345678-1234-5678-1234-567812345678}":        "12345678-1234-5678-1234-567812345678",
		"urn:uuid:12345678-1234-5678-1234-567812345678": "12345678-1234-5678-1234-567812345678",
		"1234567812345678ABCDEF7812345678":              "12345678-1234-5678-abcd-ef7812345678",
		"1234-5678-1234-5678-1234-5678-1234-5678":       "12345678-1234-5678-1234-567812345678",
	}
	for text, want := range ok {
		got, err := restoreUUIDText(text)
		if err != nil || got != want {
			t.Errorf("UUID(%q) = %q, %v; want %q", text, got, err, want)
		}
	}
	for _, text := range []string{"", "1234", "12345678-1234-5678-1234-56781234567g", "12345678-1234-5678-1234-5678123456789"} {
		if _, err := restoreUUIDText(text); !errors.Is(err, errRestoreDamaged) {
			t.Errorf("UUID(%q) error = %v, want damaged", text, err)
		}
	}
	// int(hex, 16) accepts a 0x prefix, signs and underscores: handed off.
	if _, err := restoreUUIDText("0x345678123456781234567812345678"); !errors.Is(err, errConsumerImportUnsupported) {
		t.Errorf("0x prefix error = %v", err)
	}
}

func TestRestoreToPython(t *testing.T) {
	number := func(text string) any { return json.Number(text) }
	cases := []struct {
		kind  string
		null  bool
		value any
		want  error
	}{
		{"bool", false, true, nil}, {"bool", false, number("1"), nil}, {"bool", false, number("0.0"), nil},
		{"bool", false, "t", nil}, {"bool", false, "False", nil}, {"bool", false, number("2"), errRestoreDamaged},
		{"bool", false, "yes", errRestoreDamaged}, {"bool", false, nil, errRestoreDamaged}, {"bool", true, nil, nil},
		{"bool", true, "", nil},
		{"int", false, number("12"), nil}, {"int", false, " 12 ", nil}, {"int", false, "1_000", nil}, {"int", false, "1.5", errRestoreDamaged},
		{"int", false, number("1.5"), nil}, {"int", false, true, nil}, {"int", false, []any{}, errRestoreDamaged},
		{"uuid", false, number("5"), nil}, {"uuid", false, number("-1"), errRestoreDamaged}, {"uuid", false, number("1.5"), errRestoreDamaged},
		{"uuid", false, true, nil}, {"uuid", false, []any{}, errRestoreDamaged},
		{"date", false, "2026-02-29", errRestoreDamaged}, {"date", false, "2024-02-29", nil}, {"date", false, number("1"), errRestoreDamaged},
		{"datetime", false, "2026-01-02T03:04:05.123456+00:00", nil}, {"datetime", false, "2026-13-02T03:04:05", errRestoreDamaged},
		{"datetime", false, "2026-01-02", nil}, {"time", false, "09:30:15", nil}, {"time", false, "25:00", errRestoreDamaged},
		{"decimal", false, "10.50", nil}, {"decimal", false, "abc", errRestoreDamaged}, {"decimal", false, "NaN", nil},
		{"decimal", false, number("1.5"), nil}, {"decimal", false, []any{}, errRestoreDamaged},
		{"text", false, []any{}, nil}, {"json", false, "x", nil},
	}
	for _, c := range cases {
		if got := restoreToPython(c.kind, "", c.null, c.value); !errors.Is(got, c.want) && !(got == nil && c.want == nil) {
			t.Errorf("to_python(%s, %v) = %v, want %v", c.kind, c.value, got, c.want)
		}
	}
}

func TestRestoreIdentity(t *testing.T) {
	if got, err := restoreIdentity("int", json.Number("7")); err != nil || got != "7" {
		t.Errorf("int identity = %q, %v", got, err)
	}
	if got, err := restoreIdentity("int", " 0007 "); err != nil || got != "7" {
		t.Errorf("str int identity = %q, %v", got, err)
	}
	if got, err := restoreIdentity("uuid", "ABCDEF00-1234-5678-1234-567812345678"); err != nil || got != "abcdef00-1234-5678-1234-567812345678" {
		t.Errorf("uuid identity = %q, %v", got, err)
	}
}
