package codicefiscale

import "testing"

// TestSlugifyInvalidBytes covers text whose bytes are not the UTF-8 of a
// Python str: Django decodes request input with errors='replace', so each
// maximal ill-formed subpart reaches the library as one U+FFFD. text-unidecode
// maps U+FFFD to the empty string, so such a character disappears from the
// slug. The want values are slugify() of the replaced str as computed with
// python-slugify 8.0.4 (e.g. slugify('Ros\ufffdsi') == 'rossi').
func TestSlugifyInvalidBytes(t *testing.T) {
	cases := []struct{ text, want string }{
		{"Ros\xffsi", "rossi"},      // slugify("Ros\ufffdsi")
		{"Ros\xc3\x28si", "ros-si"}, // slugify("Ros\ufffd(si")
		{"Ros\xe2\x82si", "rossi"},  // one U+FFFD: slugify("Ros\ufffdsi")
		{"Ros\x80\x80si", "rossi"},  // slugify("Ros\ufffd\ufffdsi")
		{"Roma\xff", "roma"},        // slugify("Roma\ufffd")
		{"\xff", ""},                // slugify("\ufffd")
		{"\xffr Ossi", "r-ossi"},    // slugify("\ufffdr Ossi")
	}
	for _, tc := range cases {
		if got := slugify(tc.text); got != tc.want {
			t.Errorf("slugify(%q) = %q; want %q", tc.text, got, tc.want)
		}
	}
}

// TestSlugifySurrogates keeps the code points that do reach Go as text: the
// three-byte form of a lone surrogate. python-slugify 8.0.4 gives
// slugify('Ros\ud800si') == 'ros-si' and slugify('Ros\udc80si') == 'ros-si'.
func TestSlugifySurrogates(t *testing.T) {
	for _, code := range []rune{0xd800, 0xdc80} {
		text := pyString([]rune{'R', 'o', 's', code, 's', 'i'})
		if got := slugify(text); got != "ros-si" {
			t.Errorf("slugify(%q) = %q; want ros-si", text, got)
		}
	}
}
