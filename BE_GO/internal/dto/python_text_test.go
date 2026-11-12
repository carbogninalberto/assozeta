package dto

import "testing"

// Expected values are Python 3.13 str.lower() and bytes.decode('utf-8', 'replace').
func TestPythonLowerAndReplace(t *testing.T) {
	for in, want := range map[string]string{
		"\u03a9mega": "\u03c9mega", "\u0130stanbul": "i\u0307stanbul", "\u03a3\u0391\u03a3": "\u03c3\u03b1\u03c2",
		"\u0391\u03a3 \u03a3\u0391\u03a3.": "\u03b1\u03c2 \u03c3\u03b1\u03c2.", "\u03a3\u0391\u03a3\u00b7\u0391": "\u03c3\u03b1\u03c3\u00b7\u03b1",
		"\u03a3": "\u03c3", "a\u03a3": "a\u03c2", "\u0391\u03a3\u0301": "\u03b1\u03c2\u0301", "\u01c5": "\u01c6",
	} {
		if got := PythonLower(in); got != want {
			t.Errorf("lower %q: %q, want %q", in, got, want)
		}
	}
	for in, want := range map[string]string{
		"a\xffb": "a\ufffdb", "\xe2\x82A": "\ufffdA", "\xe2\x82": "\ufffd", "\xf0\x9f\x98": "\ufffd", "\xed\xa0\x80": "\ufffd\ufffd\ufffd",
		"\xc0\xaf": "\ufffd\ufffd", "\xf4\x90\x80\x80": "\ufffd\ufffd\ufffd\ufffd", "\xe0\x80\x80": "\ufffd\ufffd\ufffd",
		"ok\xc3": "ok\ufffd", "\x80\x80": "\ufffd\ufffd",
	} {
		if got := PythonUTF8Replace(in); got != want {
			t.Errorf("replace %q: %q, want %q", in, got, want)
		}
	}
}
