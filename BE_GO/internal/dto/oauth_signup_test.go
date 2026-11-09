package dto

import "testing"

// re.search(r'^(?=.*[A-Z])(?=.*[!@#$&\.\-\_*])(?=.*[0-9]).{10,}$', text) outcomes.
func TestOAuthPasswordMatches(t *testing.T) {
	for text, want := range map[string]bool{
		"Valid-Pass1!":              true,
		"Valid-Pass1!\n":            true,  // $ before a final newline
		"Valid-Pass1!\n\n":          false, // only one final newline
		"Valid-Pass1!\nsecond line": false,
		"Valid\n-Pass1!xx":          false, // the lookaheads see the first line only
		"Va-Pass1!":                 false, // nine characters
		"valid-pass1!":              false,
		"Valid-Pass!":               false,
		"ValidPass12":               false,
		"  Valid-Pass1!  ":          true,
		"Àèìòù-Pass1":               true, // code points, not bytes
		"Ünïcödé1*":                 false,
		"\rValid-Pass1!":            true,
	} {
		if got := OAuthPasswordMatches(text); got != want {
			t.Errorf("%q: got %v, want %v", text, got, want)
		}
	}
}
