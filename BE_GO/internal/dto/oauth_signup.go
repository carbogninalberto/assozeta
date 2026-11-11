package dto

import (
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Validation codes of UserSerializerSignup beyond the shared DRF ones.
const (
	OAuthUsernameInvalid = "oauth-username-invalid" // UnicodeUsernameValidator
	OAuthUsernameUnique  = "oauth-username-unique"  // UniqueValidator with the model's message
	OAuthPasswordPattern = "oauth-password-pattern" // RegexField
)

// OAuthSignupFields is UserSerializerSignup's validated data. FirstName and
// LastName are nil when absent (the view's validated_data[...] then raises).
type OAuthSignupFields struct {
	FirstName, LastName *string
	Email               string
	Username            string
	// UsernameChecked: the username reached the field validators, so the
	// service runs the UniqueValidator query between the character and length checks.
	UsernameChecked bool
	// UsernameNUL: the username holds a NUL character, which the uniqueness query cannot carry.
	UsernameNUL bool
}

// OAuthSignupValidation holds field errors in the serializer's field order:
// first_name, last_name, email, phone, username, password.
type OAuthSignupValidation struct {
	Errors map[string][]string
	// UsernameInvalid is UnicodeUsernameValidator's result; the service
	// places UniqueValidator's error after it.
	UsernameInvalid bool
}

// oauthUsernameValid is UnicodeUsernameValidator, r'^[\w.@+-]+\Z': Python's \w is every letter and number plus '_'.
func oauthUsernameValid(text string) bool {
	if text == "" {
		return false
	}
	for _, r := range text {
		if !unicode.IsLetter(r) && !unicode.IsNumber(r) && !strings.ContainsRune("_.@+-", r) {
			return false
		}
	}
	return true
}

// IsASCII reports text without non-ASCII code points (Python's Unicode
// case mapping and \w classes are not ported).
func IsASCII(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// OAuthPasswordMatches is re.search(r'^(?=.*[A-Z])(?=.*[!@#$&\.\-\_*])(?=.*[0-9]).{10,}$', text):
// the first line (up to a newline) holds an upper-case letter, one of
// !@#$&.-_* and a digit, has at least ten characters, and is the whole text
// or is followed only by one final newline.
func OAuthPasswordMatches(text string) bool {
	line, rest, newline := strings.Cut(text, "\n")
	if newline && rest != "" {
		return false
	}
	return utf8.RuneCountInString(line) >= 10 && strings.ContainsAny(line, "ABCDEFGHIJKLMNOPQRSTUVWXYZ") &&
		strings.ContainsAny(line, "!@#$&.-_*") && strings.ContainsAny(line, "0123456789")
}

// ParseOAuthSignup runs UserSerializerSignup's field validation except the
// username UniqueValidator (a database query; see OAuthSignupFields).
// unported reports input outside the port (non-ASCII usernames, NUL in the
// username: the UniqueValidator query raises in Django).
func ParseOAuthSignup(object domain.RequestObject) (fields OAuthSignupFields, validation OAuthSignupValidation, unported bool) {
	validation.Errors = map[string][]string{}
	text := func(name string, field SupplierField) *string {
		value, present := object.Lookup(name)
		if !present {
			if field.Required {
				validation.Errors[name] = []string{PaymentCategoryValidationRequired}
			}
			return nil
		}
		result, codes := modelTextValue(field, value, false)
		if len(codes) > 0 {
			validation.Errors[name] = codes
			return nil
		}
		return result
	}
	fields.FirstName = text("first_name", SupplierField{Name: "first_name", Max: 150, Blank: true})
	fields.LastName = text("last_name", SupplierField{Name: "last_name", Max: 150, Blank: true})
	if email := text("email", SupplierField{Name: "email", Max: 254, Blank: true, Email: true}); email != nil {
		fields.Email = *email
	}
	text("phone", SupplierField{Name: "phone", Max: 20, Null: true, Blank: true})
	if value, present := object.Lookup("username"); !present {
		validation.Errors["username"] = []string{PaymentCategoryValidationRequired}
	} else {
		username, codes := parsePaymentCategoryString(value, 150)
		if len(codes) == 1 && (codes[0] == PaymentCategoryValidationNull || codes[0] == PaymentCategoryValidationString ||
			codes[0] == PaymentCategoryValidationBlank) {
			validation.Errors["username"] = codes
		} else {
			fields.Username, fields.UsernameChecked = username, true
			fields.UsernameNUL = strings.ContainsRune(username, 0)
			validation.UsernameInvalid = !oauthUsernameValid(username)
			if len(codes) > 0 {
				validation.Errors["username"] = codes
			}
		}
	}
	if value, present := object.Lookup("password"); !present {
		validation.Errors["password"] = []string{PaymentCategoryValidationRequired}
	} else {
		password, codes := parsePaymentCategoryString(value, int(^uint(0)>>1))
		if len(codes) == 1 && (codes[0] == PaymentCategoryValidationNull || codes[0] == PaymentCategoryValidationString ||
			codes[0] == PaymentCategoryValidationBlank) {
			validation.Errors["password"] = codes
		} else if !OAuthPasswordMatches(password) {
			validation.Errors["password"] = append(codes, OAuthPasswordPattern)
		} else if len(codes) > 0 {
			validation.Errors["password"] = codes
		}
	}
	return fields, validation, false
}
