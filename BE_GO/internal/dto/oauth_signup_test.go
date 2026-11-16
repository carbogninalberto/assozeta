package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Python's full case mappings used by the signup view: str.upper() for the
// stored username and str.lower() for the e-mail domain and welcome recipient.
func TestOAuthPythonCaseMapping(t *testing.T) {
	for value, want := range map[string]string{
		"ß": "SS", "straße": "STRASSE", "İ": "İ", "ı": "I",
	} {
		if got := PythonUpper(value); got != want {
			t.Errorf("PythonUpper(%q) = %q, want %q", value, got, want)
		}
	}
	for value, want := range map[string]string{
		"İ": "i\u0307", "ΟΣ": "ος", "ΣΣ": "σς", "STRASSE": "strasse",
	} {
		if got := PythonLower(value); got != want {
			t.Errorf("PythonLower(%q) = %q, want %q", value, got, want)
		}
	}
}

// The email field keeps a Unicode domain (EmailValidator's hostname range) and
// rejects a Unicode local part other than the code points re.IGNORECASE folds
// into its ASCII letter classes.
func TestParseOAuthSignupEmailUnicode(t *testing.T) {
	signup := func(email string) OAuthSignupValidation {
		object := domain.RequestObject{
			{Name: "first_name", Value: "Nome"}, {Name: "last_name", Value: "Cognome"}, {Name: "email", Value: email},
			{Name: "username", Value: "persona-1"}, {Name: "password", Value: "Valid-Pass1!"},
		}
		_, validation := ParseOAuthSignup(object)
		return validation
	}
	for _, email := range []string{"Ana@Bücher.EXAMPLE", "İ@Example.TEST", "ı@Example.TEST", `"İ"@example.test`} {
		if validation := signup(email); len(validation.Errors) != 0 {
			t.Fatalf("%q rejected: %v", email, validation.Errors)
		}
	}
	for _, email := range []string{"Zoë@example.test", "Üser@example.test"} {
		validation := signup(email)
		if got := strings.Join(validation.Errors["email"], ","); got != SupplierValidationEmail {
			t.Fatalf("%q: %q", email, got)
		}
	}
}

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
