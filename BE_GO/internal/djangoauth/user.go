package djangoauth

import (
	"regexp"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

const (
	invalidEmail    = "Inserisci un indirizzo email valido."
	invalidUsername = "Immetti un nome utente valido. Questo campo può contenere solo lettere dell'alfabeto inglese, numeri e caratteri @/./+/-/_ ."
)

// usernamePattern is UnicodeUsernameValidator: ^[\w.@+-]+\Z with Unicode \w.
var usernamePattern = regexp.MustCompile(`^[\p{L}\p{N}_.@+-]+$`)

// ValidateEmail is django.core.validators.validate_email; it returns the
// message of the ValidationError it raises.
func ValidateEmail(value string) (string, bool) {
	if dto.SupplierEmail(value) {
		return "", true
	}
	return invalidEmail, false
}

// UserField is one entry of create_instance_superuser's values dict.
type UserField struct{ Name, Value string }

// CleanUserField is User._meta.get_field(name).clean(value, None) for the
// command's fields: blank values skip the validators; otherwise every
// validator runs in the field's order and all messages are returned.
func CleanUserField(field UserField) []string {
	if field.Value == "" {
		return nil
	}
	length := runeCount(field.Value)
	var messages []string
	switch field.Name {
	case "username":
		if !usernamePattern.MatchString(field.Value) {
			messages = append(messages, invalidUsername)
		}
		if length > 150 {
			messages = append(messages, maxLength(150, length))
		}
	case "email":
		if _, ok := ValidateEmail(field.Value); !ok {
			messages = append(messages, invalidEmail)
		}
		if length > 254 {
			messages = append(messages, maxLength(254, length))
		}
	case "first_name", "last_name":
		if length > 150 {
			messages = append(messages, maxLength(150, length))
		}
	case "phone":
		if length > 20 {
			messages = append(messages, maxLength(20, length))
		}
	}
	return messages
}
