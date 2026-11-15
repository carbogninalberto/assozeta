package smtpsend

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// TestEncodedWordCharsets: codecs.lookup's names (CPython 3.13.16) decide the
// path. UTF-8, US-ASCII and ISO-8859-1 under any spelling are decoded, a name
// Python does not know (or a bytes-to-bytes codec) is unknown bytes, and any
// other text codec stays with Django.
func TestEncodedWordCharsets(t *testing.T) {
	for name, want := range map[string]string{
		"UTF-8": "utf-8", "utf 8": "utf-8", "U8": "utf-8", "us-ascii": "ascii", "ANSI_X3.4-1968": "ascii",
		"Latin-1": "iso8859-1", "l1": "iso8859-1", "  latin1 ": "iso8859-1", "bogus": "", "unknown-8bit": "",
		"base64": "", "rot13": "", "gb-2312": "", "Gb2312": "gb2312", "KOI8-R": "koi8-r", "windows-1252": "cp1252",
	} {
		if got := pythonCodecName(name); got != want {
			t.Errorf("%q: %q, want %q", name, got, want)
		}
	}
	if _, err := decodeCharset([]byte{0xc4, 0xe3}, "gb2312"); !errors.Is(err, refusal.ErrNotPorted) {
		t.Errorf("gb2312: %v, want the refusal", err)
	}
	if got, err := decodeCharset([]byte("x\xff"), "bogus"); err != nil || got != "x\xed\xb3\xbf" {
		t.Errorf("bogus: %q %v", got, err)
	}
}
