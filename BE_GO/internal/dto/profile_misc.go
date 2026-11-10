package dto

import (
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// DRFFieldError is one serializer field's error codes, in serializer field
// order; the handler renders str(ValidationError(...)).
type DRFFieldError struct {
	Field string
	Codes []string
}

// DRFErrorCode is the ErrorDetail code of a validation code from the shared
// message table.
func DRFErrorCode(code string) string {
	switch {
	case code == PaymentCategoryValidationNull, code == "no-data":
		return "null"
	case code == PaymentCategoryValidationBlank:
		return "blank"
	case code == PaymentCategoryValidationRequired:
		return "required"
	case strings.HasPrefix(code, "max:"):
		return "max_length"
	case strings.HasPrefix(code, "max-digits:"):
		return "max_digits"
	case strings.HasPrefix(code, "max-decimal-places:"):
		return "max_decimal_places"
	case strings.HasPrefix(code, "max-whole-digits:"):
		return "max_whole_digits"
	case code == DRFValidationMaxStringLength:
		return "max_string_length"
	case code == "null-character":
		return "null_characters_not_allowed"
	case strings.HasPrefix(code, "surrogate:"):
		return "surrogate_characters_not_allowed"
	}
	return "invalid"
}

// DRFErrorRepr is str(ValidationError(errors)): the repr of a dict of
// ErrorDetail lists.
func DRFErrorRepr(errors []DRFFieldError, message func(string) string) string {
	var out strings.Builder
	out.WriteByte('{')
	for i, field := range errors {
		if i > 0 {
			out.WriteString(", ")
		}
		pythonStringRepr(&out, field.Field)
		out.WriteString(": [")
		for j, code := range field.Codes {
			if j > 0 {
				out.WriteString(", ")
			}
			out.WriteString("ErrorDetail(string=")
			pythonStringRepr(&out, message(code))
			out.WriteString(", code=")
			pythonStringRepr(&out, DRFErrorCode(code))
			out.WriteByte(')')
		}
		out.WriteByte(']')
	}
	out.WriteByte('}')
	return out.String()
}

// drfSerializerData is the Serializer(data=...) non-dict check: None is
// 'No data provided', other types 'Expected a dictionary'.
func drfSerializerData(value any) (domain.RequestObject, []DRFFieldError) {
	if object, ok := value.(domain.RequestObject); ok {
		return object, nil
	}
	if value == nil {
		return nil, []DRFFieldError{{Field: "non_field_errors", Codes: []string{"no-data"}}}
	}
	return nil, []DRFFieldError{{Field: "non_field_errors", Codes: []string{"dictionary:" + pythonTypeName(value)}}}
}

// OnboardingAssociationInput is SportAssociationOnboardingStep0Serializer's
// validated_data (partial): only the supplied fields are set.
type OnboardingAssociationInput struct {
	Denomination, TaxCode          *string
	Logo, Sport                    **string
	SubscriptionFee, MembershipFee **string
}

// ParseOnboardingAssociation validates denomination (CharField 150),
// tax_code (11), logo (TextField, nullable), sport (100, nullable and blank)
// and the two DecimalField(9, 2) fees (membership_fee nullable).
func ParseOnboardingAssociation(value any) (OnboardingAssociationInput, []DRFFieldError) {
	var input OnboardingAssociationInput
	object, invalid := drfSerializerData(value)
	if invalid != nil {
		return input, invalid
	}
	var errors []DRFFieldError
	text := func(name string, field SupplierField) (*string, bool) {
		raw, present := object.Lookup(name)
		if !present {
			return nil, false
		}
		stored, codes := modelTextValue(field, raw, false)
		if len(codes) > 0 {
			errors = append(errors, DRFFieldError{Field: name, Codes: codes})
			return nil, false
		}
		return stored, true
	}
	decimal := func(name string, null bool) (*string, bool) {
		raw, present := object.Lookup(name)
		if !present {
			return nil, false
		}
		if raw == nil {
			if !null {
				errors = append(errors, DRFFieldError{Field: name, Codes: []string{PaymentCategoryValidationNull}})
				return nil, false
			}
			return nil, true
		}
		parsed, code := DRFDecimal(raw, 9, 2)
		if code != "" {
			errors = append(errors, DRFFieldError{Field: name, Codes: []string{code}})
			return nil, false
		}
		return &parsed, true
	}
	if v, ok := text("denomination", SupplierField{Name: "denomination", Max: 150}); ok {
		input.Denomination = v
	}
	if v, ok := text("tax_code", SupplierField{Name: "tax_code", Max: 11}); ok {
		input.TaxCode = v
	}
	if v, ok := text("logo", SupplierField{Name: "logo", Null: true}); ok {
		input.Logo = &v
	}
	if v, ok := text("sport", SupplierField{Name: "sport", Max: 100, Null: true, Blank: true}); ok {
		input.Sport = &v
	}
	if v, ok := decimal("subscription_fee", false); ok {
		input.SubscriptionFee = &v
	}
	if v, ok := decimal("membership_fee", true); ok {
		input.MembershipFee = &v
	}
	return input, errors
}

// OnboardingFlagFields are UsersOnboarding's BooleanFields in model order.
var OnboardingFlagFields = []string{"show", "create_membership", "view_membership", "approve_payment", "download_invoice",
	"view_collaborators", "view_settings"}

// ParseOnboardingFlags is UsersOnboardingSerializer(partial=True): only the
// boolean checklist fields are read. users_onboarding_id and user are
// read-only serializer fields, so any value sent for them is not looked at.
func ParseOnboardingFlags(value any) (map[string]bool, []DRFFieldError) {
	object, invalid := drfSerializerData(value)
	if invalid != nil {
		return nil, invalid
	}
	flags := map[string]bool{}
	var errors []DRFFieldError
	for _, name := range OnboardingFlagFields {
		raw, present := object.Lookup(name)
		if !present {
			continue
		}
		if raw == nil {
			errors = append(errors, DRFFieldError{Field: name, Codes: []string{PaymentCategoryValidationNull}})
			continue
		}
		parsed, err := parsePaymentCategoryBoolean(raw)
		if err != nil {
			errors = append(errors, DRFFieldError{Field: name, Codes: []string{PaymentCategoryValidationBoolean}})
			continue
		}
		flags[name] = parsed
	}
	return flags, errors
}

var pythonDecimalSpecial = regexp.MustCompile(`(?i)^[+-]?(nan|snan|inf|infinity)$`)

// pythonDecimalText is decimal.Decimal(str) for finite values (surrounding
// whitespace allowed): sign, coefficient digits and exponent. special
// reports NaN/Infinity; ok is false for InvalidOperation.
func pythonDecimalText(text string) (negative bool, coefficient *big.Int, exponent int, special, ok bool) {
	text = strings.TrimFunc(text, unicode.IsSpace)
	if pythonDecimalSpecial.MatchString(text) {
		return false, nil, 0, true, true
	}
	match := pythonDecimalLiteral.FindStringSubmatch(text)
	if match == nil || match[2] == "" && match[3] == "" {
		return false, nil, 0, false, false
	}
	whole, fraction := strings.ReplaceAll(match[2], "_", ""), strings.ReplaceAll(match[3], "_", "")
	if match[4] != "" {
		parsed, err := strconv.Atoi(strings.ReplaceAll(match[4], "_", ""))
		if err != nil {
			return false, nil, 0, false, false
		}
		exponent = parsed
	}
	coefficient, _ = new(big.Int).SetString("0"+whole+fraction, 10)
	return match[1] == "-", coefficient, exponent - len(fraction), false, true
}

// FreshMembershipErrors is _validate_fresh_final_membership_payload.
func FreshMembershipErrors(value any) map[string]string {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return map[string]string{"membership_data": "This field must be an object."}
	}
	errors := map[string]string{}
	for _, section := range []string{"season", "fiscal"} {
		raw, _ := object.Lookup(section)
		fields, ok := raw.(domain.RequestObject)
		if !ok {
			errors[section] = "This section is required."
			continue
		}
		for _, bound := range []struct {
			name  string
			upper int64
		}{{"day", 31}, {"month", 12}} {
			key := section + "." + bound.name
			value, _ := fields.Lookup(bound.name)
			if value == nil || value == "" {
				errors[key] = "This field is required."
				continue
			}
			negative, coefficient, exponent, special, ok := pythonDecimalText(PythonStr(value))
			if !ok || special {
				errors[key] = "This field must be a valid number."
				continue
			}
			// int(Decimal) truncates; Decimal != to_integral_value() for a
			// non-zero fractional part.
			integral, whole := true, new(big.Int).Set(coefficient)
			switch {
			case coefficient.Sign() == 0:
				whole.SetInt64(0)
			case exponent > 64:
				whole.SetInt64(bound.upper + 1)
			case exponent >= 0:
				whole.Mul(whole, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(exponent)), nil))
			case exponent < -4096:
				integral = false
			default:
				divisor := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(-exponent)), nil)
				remainder := new(big.Int)
				whole.QuoRem(whole, divisor, remainder)
				integral = remainder.Sign() == 0
			}
			if negative {
				whole.Neg(whole)
			}
			if !integral {
				errors[key] = "This field must be a whole number."
				continue
			}
			if whole.Cmp(big.NewInt(1)) < 0 || whole.Cmp(big.NewInt(bound.upper)) > 0 {
				errors[key] = "This field must be between 1 and " + strconv.FormatInt(bound.upper, 10) + "."
			}
		}
	}
	for _, name := range []string{"subscription_fee", "membership_fee"} {
		value, _ := object.Lookup(name)
		if value == nil || value == "" {
			errors[name] = "This field is required."
			continue
		}
		_, _, _, special, ok := pythonDecimalText(strings.ReplaceAll(PythonStr(value), ",", "."))
		if !ok || special {
			errors[name] = "This field must be a valid amount."
		}
	}
	return errors
}

// PythonFloatRepr is repr(float(value)) for a value DecimalField already
// accepted (a string or a JSON number); ok is false otherwise.
func PythonFloatRepr(value any) (string, bool) {
	var text string
	switch v := value.(type) {
	case string:
		text = strings.ReplaceAll(strings.TrimFunc(v, unicode.IsSpace), "_", "")
	case json.Number:
		text = string(v)
	default:
		return "", false
	}
	number, err := strconv.ParseFloat(text, 64)
	if err != nil || number != number || number > 1e16 || number < -1e16 {
		return "", false
	}
	if number != 0 && number < 1e-4 && number > -1e-4 {
		return "", false // repr would use an exponent
	}
	repr := strconv.FormatFloat(number, 'f', -1, 64)
	if !strings.ContainsAny(repr, ".") {
		repr += ".0"
	}
	return repr, true
}

var pythonASCIIInt = regexp.MustCompile(`^[ \t\n\r\v\f]*[+-]?\d+(?:_\d+)*[ \t\n\r\v\f]*$`)

// PythonPositiveSmallInt is IntegerField.get_prep_value (int(value)) for the
// values this port stores: JSON integers and ASCII integer strings within the
// PositiveSmallIntegerField range. invalid reports a string int() rejects
// (Django's "expected a number" ValueError); supported is false for every
// other value, which callers refuse.
func PythonPositiveSmallInt(value any) (number int64, invalid, supported bool) {
	var text string
	switch v := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return 0, false, false
		}
		text = string(v)
	case string:
		for _, r := range v {
			if r > unicode.MaxASCII {
				return 0, false, false
			}
		}
		if !pythonASCIIInt.MatchString(v) {
			return 0, true, true
		}
		text = strings.ReplaceAll(strings.TrimSpace(v), "_", "")
	default:
		return 0, false, false
	}
	parsed, err := strconv.ParseInt(text, 10, 64)
	if err != nil || parsed < 0 || parsed > 32767 {
		return 0, false, false
	}
	return parsed, false, true
}
