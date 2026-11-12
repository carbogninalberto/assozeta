// Package djangoauth reproduces the Django 5.2 account checks that the
// create_instance_superuser management command runs before it writes:
// validate_password with the project's AUTH_PASSWORD_VALIDATORS, the User
// model field validators and the manager's username/email normalisation.
package djangoauth

import (
	"strings"
	"unicode"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

// PythonLower is str.lower (full case mapping, final sigma).
func PythonLower(value string) string { return cases.Lower(language.Und).String(value) }

// PythonUpper is str.upper (full case mapping: ß becomes SS).
func PythonUpper(value string) string { return cases.Upper(language.Und).String(value) }

// pythonSpace is str.isspace for Python 3.13 (Unicode 15.1).
func pythonSpace(r rune) bool {
	switch r {
	case '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, ' ', 0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

// PythonStrip is str.strip() without arguments.
func PythonStrip(value string) string { return strings.TrimFunc(value, pythonSpace) }

// pythonWord is the re module's Unicode \w: letters, numbers and '_'.
func pythonWord(r rune) bool { return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) }

// digitRanges are the str.isdigit characters outside category Nd (Numeric_Type=Digit).
var digitRanges = [][2]rune{{0xb2, 0xb3}, {0xb9, 0xb9}, {0x1369, 0x1371}, {0x19da, 0x19da}, {0x2070, 0x2070},
	{0x2074, 0x2079}, {0x2080, 0x2089}, {0x2460, 0x2468}, {0x2474, 0x247c}, {0x2488, 0x2490}, {0x24ea, 0x24ea},
	{0x24f5, 0x24fd}, {0x24ff, 0x24ff}, {0x2776, 0x277e}, {0x2780, 0x2788}, {0x278a, 0x2792}, {0x10a40, 0x10a43},
	{0x10e60, 0x10e68}, {0x11052, 0x1105a}, {0x1f100, 0x1f10a}}

// pythonIsDigit is str.isdigit: non-empty and every character a digit.
func pythonIsDigit(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if unicode.Is(unicode.Nd, r) {
			continue
		}
		found := false
		for _, span := range digitRanges {
			if r >= span[0] && r <= span[1] {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}
	return true
}

// NormalizeUsername is AbstractBaseUser.normalize_username (NFKC).
func NormalizeUsername(value string) string { return norm.NFKC.String(value) }

// NormalizeEmail is BaseUserManager.normalize_email: the domain lowercased.
func NormalizeEmail(email string) string {
	at := strings.LastIndex(email, "@")
	if at < 0 {
		return email
	}
	return email[:at] + "@" + PythonLower(email[at+1:])
}

func runeCount(value string) int { return len([]rune(value)) }
