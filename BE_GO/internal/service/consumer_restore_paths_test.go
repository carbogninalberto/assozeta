package service

import "testing"

// TestRestoreEntryPathRules pins _validate_entries' path refusal (absolute,
// "..", a Windows drive, and the backslash media rule); the expectations come
// from running Django's checks under CPython 3.13.
func TestRestoreEntryPathRules(t *testing.T) {
	cases := []struct {
		name    string
		refused bool
	}{
		{`files/documents/x/a\b.pdf`, false}, {`files/documents/x/..\a.pdf`, true}, {`files/documents/x/a\..`, true},
		{`files/documents/x/\a`, true}, {`files/documents/x/c:a`, false}, {`files/documents/x/é:a`, false},
		{`files/documents/x\y/a.pdf`, true}, {`files/documents/x/a\b/c`, true}, {`data\x.json`, true},
		{`files//x/a\b`, true}, {`files/documents/x/a\\b`, false}, {`files/documents/x/a\.\b`, false},
		{`files/documents/x/\\srv\a`, true}, {`1:x`, true}, {`é:x`, true}, {`:x`, false}, {`\\srv\share`, true},
		{`\x`, true}, {`a:`, true}, {`x/a:b`, false}, {`files/documents/x/a:\b`, true}, {`files/documents/x/.\a`, false},
		{`docs/a.pdf`, false}, {`files/documents/x/a\:b`, false},
	}
	for _, c := range cases {
		refused := c.name[0] == '/' || restoreContains(restorePosixParts(c.name), "..") || restoreWindowsDrive(c.name) ||
			containsBackslash(c.name) && !restoreBackslashMedia(c.name)
		if refused != c.refused {
			t.Errorf("%q refused = %v, want %v", c.name, refused, c.refused)
		}
	}
}

func containsBackslash(name string) bool {
	for i := 0; i < len(name); i++ {
		if name[i] == '\\' {
			return true
		}
	}
	return false
}

// TestRestoreUpper is str.upper() with the full case mapping.
func TestRestoreUpper(t *testing.T) {
	for text, want := range map[string]string{"ß": "SS", "ﬁx": "FIX", "ǆ": "Ǆ", "aaa": "AAA"} {
		if got := restoreUpper(text); got != want {
			t.Errorf("restoreUpper(%q) = %q, want %q", text, got, want)
		}
	}
	if got := restoreFormatSizeUint(2048); got != "2.00 KiB" {
		t.Errorf("restoreFormatSizeUint(2048) = %q", got)
	}
	if got := restoreFormatSizeUint(1 << 63); got != "8589934592.00 GiB" {
		t.Errorf("restoreFormatSizeUint(1<<63) = %q", got)
	}
}
