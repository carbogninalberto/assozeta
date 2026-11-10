package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// --- PaymentEntrySerializer ---------------------------------------------------

// paymentEntryOrder is PaymentEntrySerializer's Meta.fields order (errors
// are reported in it).
var paymentEntryOrder = []string{"payment_id", "type", "subject", "amount", "user", "description", "expense", "paid", "payment_date",
	"payment_category", "sport_association", "custom_accounts", "notes", "course", "meta_payment_categories", "creation_date", "attachments",
	"attachments_data"}

// PaymentTypes are Payment.TYPE_CHOICES keys in order.
var PaymentTypes = []string{"default", "cash", "transfer", "online", "sepa-transfer", "stripe", "pos"}

// PaymentEntryFieldError is one field of serializer.errors: Codes for the
// field itself or Items (ListField child errors by index, in order).
type PaymentEntryFieldError struct {
	Name  string
	Codes []string
	Items []PaymentEntryItemError
}

type PaymentEntryItemError struct {
	Index int
	Codes []string
}

// PaymentEntryValidationError is serializer.errors in field order.
type PaymentEntryValidationError struct{ Fields []PaymentEntryFieldError }

func (e *PaymentEntryValidationError) Error() string { return "invalid payment entry input" }

func (e *PaymentEntryValidationError) add(field PaymentEntryFieldError) {
	e.Fields = append(e.Fields, field)
}

// PaymentEntryDRFCode is the ErrorDetail code of a validation code.
func PaymentEntryDRFCode(code string) string {
	switch {
	case code == PaymentCategoryValidationRequired, code == PaymentCategoryValidationNull, code == PaymentCategoryValidationBlank:
		return code
	case strings.HasPrefix(code, "max:"):
		return "max_length"
	case code == "null-character":
		return "null_characters_not_allowed"
	case strings.HasPrefix(code, "surrogate:"):
		return "surrogate_characters_not_allowed"
	case strings.HasPrefix(code, "choice:"):
		return "invalid_choice"
	case code == DRFValidationMaxStringLength:
		return "max_string_length"
	case strings.HasPrefix(code, "max-digits:"):
		return "max_digits"
	case strings.HasPrefix(code, "max-decimal-places:"):
		return "max_decimal_places"
	case strings.HasPrefix(code, "max-whole-digits:"):
		return "max_whole_digits"
	case code == PaymentCategoryValidationDateTimeOverflow:
		return "overflow"
	case strings.HasPrefix(code, "related-does-not-exist:"):
		return "does_not_exist"
	case strings.HasPrefix(code, "related-incorrect-type:"):
		return "incorrect_type"
	case strings.HasPrefix(code, "not-a-list:"):
		return "not_a_list"
	case strings.HasPrefix(code, "not-a-dict:"):
		return "not_a_dict"
	}
	return "invalid"
}

// PaymentEntryDate is a date value the add view already replaced with a
// datetime (now, or a strptime('%Y-%m-%d') match): DRF only makes it aware.
type PaymentEntryDate struct{ Moment time.Time }

// PaymentEntryOptionalText is a present nullable text field (nil is None).
type PaymentEntryOptionalText struct {
	Present bool
	Value   *string
}

// PaymentEntryJSON is a present JSONField value (parsed JSON, nil is None).
type PaymentEntryJSON struct {
	Present bool
	Value   any
}

// PaymentEntryInput is serializer.validated_data before the related-field
// lookups (the service checks existence and the tenant scope).
type PaymentEntryInput struct {
	Type                           *string
	Subject                        *int
	Amount                         *string // quantized text
	Description, Notes             PaymentEntryOptionalText
	Expense, Paid                  *bool
	PaymentDatePresent             bool
	PaymentDate                    *time.Time
	Category, Association, Account InvoicePartyRelated
	Course, Meta                   PaymentEntryJSON
	CreationDate                   *time.Time
	AttachmentsPresent             bool
	Attachments                    []domain.RequestObject
}

func paymentEntryChoice(value any, choices []string) (string, string) {
	if value == nil {
		return "", PaymentCategoryValidationNull
	}
	text := PythonStr(value)
	for _, choice := range choices {
		if text == choice {
			return choice, ""
		}
	}
	return "", "choice:" + text
}

func paymentEntryDate(value any, null bool) (*time.Time, string) {
	if date, ok := value.(PaymentEntryDate); ok {
		moment := date.Moment.UTC()
		return &moment, ""
	}
	return drfDateTime(value, null)
}

// ParsePaymentEntry ports PaymentEntrySerializer field validation (partial
// for the update view). payment_id and the nested user are refused by the
// service before parsing.
func ParsePaymentEntry(object domain.RequestObject, partial bool) (PaymentEntryInput, *PaymentEntryValidationError) {
	input := PaymentEntryInput{}
	invalid := &PaymentEntryValidationError{}
	for _, name := range paymentEntryOrder {
		value, present := object.Lookup(name)
		if !present {
			if name == "creation_date" && !partial {
				invalid.add(PaymentEntryFieldError{Name: name, Codes: []string{PaymentCategoryValidationRequired}})
			}
			continue
		}
		fail := func(codes ...string) { invalid.add(PaymentEntryFieldError{Name: name, Codes: codes}) }
		switch name {
		case "type":
			if choice, code := paymentEntryChoice(value, PaymentTypes); code != "" {
				fail(code)
			} else {
				input.Type = &choice
			}
		case "subject":
			if choice, code := paymentEntryChoice(value, []string{"0", "1", "2", "3"}); code != "" {
				fail(code)
			} else {
				subject, _ := strconv.Atoi(choice)
				input.Subject = &subject
			}
		case "amount":
			if text, code := drfDecimal(value, 9, 2); code != "" {
				fail(code)
			} else {
				input.Amount = &text
			}
		case "description", "notes":
			text, codes := modelTextValue(SupplierField{Name: name, Max: 5120, Null: true, Blank: name == "notes"}, value, false)
			if len(codes) > 0 {
				fail(codes...)
			} else if name == "description" {
				input.Description = PaymentEntryOptionalText{Present: true, Value: text}
			} else {
				input.Notes = PaymentEntryOptionalText{Present: true, Value: text}
			}
		case "expense", "paid":
			if flag, code := drfBoolean(value, false); code != "" {
				fail(code)
			} else if name == "expense" {
				input.Expense = flag
			} else {
				input.Paid = flag
			}
		case "payment_date":
			if moment, code := paymentEntryDate(value, true); code != "" {
				fail(code)
			} else {
				input.PaymentDatePresent, input.PaymentDate = true, moment
			}
		case "creation_date":
			if moment, code := paymentEntryDate(value, false); code != "" {
				fail(code)
			} else {
				input.CreationDate = moment
			}
		case "payment_category", "sport_association", "custom_accounts":
			related, code := drfRelated(value, true)
			if code != "" {
				fail(code)
				continue
			}
			switch name {
			case "payment_category":
				input.Category = related
			case "sport_association":
				input.Association = related
			default:
				input.Account = related
			}
		case "course":
			input.Course = PaymentEntryJSON{Present: true, Value: value}
		case "meta_payment_categories":
			input.Meta = PaymentEntryJSON{Present: true, Value: value}
		case "attachments_data":
			if value == nil {
				fail(PaymentCategoryValidationNull)
				continue
			}
			items, ok := value.([]any)
			if !ok {
				fail("not-a-list:" + pythonTypeName(value))
				continue
			}
			var errors []PaymentEntryItemError
			attachments := make([]domain.RequestObject, 0, len(items))
			for index, item := range items {
				object, ok := item.(domain.RequestObject)
				if !ok {
					errors = append(errors, PaymentEntryItemError{Index: index, Codes: []string{"not-a-dict:" + pythonTypeName(item)}})
					continue
				}
				attachments = append(attachments, object)
			}
			if errors != nil {
				invalid.add(PaymentEntryFieldError{Name: name, Items: errors})
				continue
			}
			input.AttachmentsPresent, input.Attachments = true, attachments
		}
	}
	if len(invalid.Fields) > 0 {
		return input, invalid
	}
	return input, nil
}

// --- Python numbers -----------------------------------------------------------

// PythonFloatOf is float(value) of a parsed JSON value: ok false is the
// TypeError/ValueError/OverflowError the view does not handle.
func PythonFloatOf(value any) (float64, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return 1, true
		}
		return 0, true
	case string:
		return PythonFloatParse(v)
	case json.Number:
		text := string(v)
		if strings.ContainsAny(text, ".eE") {
			number, _ := strconv.ParseFloat(text, 64)
			return number, true
		}
		integer, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return 0, false
		}
		number, accuracy := new(big.Float).SetInt(integer).Float64()
		if math.IsInf(number, 0) || (accuracy != big.Exact && math.Abs(number) == math.MaxFloat64) {
			// float(int) raises OverflowError past the float range.
			return 0, false
		}
		return number, true
	}
	return 0, false
}

// PaymentFloatHalfEven is DRF DecimalField.to_representation of a float:
// Decimal(str(value)).quantize(Decimal('0.01')) with ROUND_HALF_EVEN.
func PaymentFloatHalfEven(value float64) string {
	rat, ok := new(big.Rat).SetString(pythonFloatRepr(value))
	if !ok {
		return ""
	}
	return ratHalfEven(rat, 2)
}

func ratHalfEven(value *big.Rat, places int) string {
	scale := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(places)), nil)
	scaled := new(big.Rat).Mul(value, new(big.Rat).SetInt(scale))
	negative := scaled.Sign() < 0
	scaled.Abs(scaled)
	quotient, remainder := new(big.Int).QuoRem(scaled.Num(), scaled.Denom(), new(big.Int))
	twice := new(big.Int).Mul(remainder, big.NewInt(2))
	switch twice.Cmp(scaled.Denom()) {
	case 1:
		quotient.Add(quotient, big.NewInt(1))
	case 0:
		if quotient.Bit(0) == 1 {
			quotient.Add(quotient, big.NewInt(1))
		}
	}
	digits := quotient.String()
	for len(digits) <= places {
		digits = "0" + digits
	}
	text := digits[:len(digits)-places] + "." + digits[len(digits)-places:]
	if negative {
		text = "-" + text
	}
	return text
}

// PythonDecimal is decimal.Decimal(text): Kind is "finite", "inf", "nan"
// (quiet or signalling) or "" (InvalidOperation). Text is str() of a finite
// value (the parameter Django passes to the numeric column).
type PythonDecimal struct {
	Kind     string
	Negative bool
	Zero     bool
	Text     string
	// Signaling and Payload describe a NaN: sNaN, and its diagnostic digits without leading zeros.
	Signaling bool
	Payload   string
}

var pythonDecimalGrammar = regexp.MustCompile(`(?i)^([+-])?(?:(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?|(inf|infinity)|(s?nan)\d*)$`)

// ParsePythonDecimal is decimal.Decimal(text) of the C implementation:
// underscores are dropped, Unicode digits mapped and surrounding whitespace
// stripped before the grammar.
func ParsePythonDecimal(text string) PythonDecimal {
	var mapped strings.Builder
	for _, r := range text {
		switch {
		case r == '_':
			continue
		case r > 0 && r <= 127:
			mapped.WriteRune(r)
		case unicode.IsSpace(r):
			mapped.WriteRune(' ')
		default:
			digit, ok := pythonDigit(r)
			if !ok {
				return PythonDecimal{}
			}
			mapped.WriteRune('0' + rune(digit))
		}
	}
	trimmed := strings.Trim(mapped.String(), " \t\n\r\v\f")
	match := pythonDecimalGrammar.FindStringSubmatch(trimmed)
	if match == nil {
		return PythonDecimal{}
	}
	negative := match[1] == "-"
	switch {
	case match[5] != "":
		return PythonDecimal{Kind: "inf", Negative: negative}
	case match[6] != "":
		rest := strings.ToLower(strings.TrimLeft(trimmed, "+-"))
		signaling := strings.HasPrefix(rest, "s")
		payload := strings.TrimLeft(strings.TrimPrefix(strings.TrimPrefix(rest, "s"), "nan"), "0")
		return PythonDecimal{Kind: "nan", Negative: negative, Signaling: signaling, Payload: payload}
	}
	whole, fraction := match[2], match[3]
	if whole == "" && fraction == "" {
		return PythonDecimal{}
	}
	exponent := new(big.Int)
	if match[4] != "" {
		exponent.SetString(strings.TrimPrefix(match[4], "+"), 10)
	}
	exponent.Sub(exponent, big.NewInt(int64(len(fraction))))
	coefficient := strings.TrimLeft(whole+fraction, "0")
	if coefficient == "" {
		coefficient = "0"
	}
	if !exponent.IsInt64() {
		return PythonDecimal{}
	}
	result := PythonDecimal{Kind: "finite", Negative: negative, Zero: coefficient == "0", Text: decimalSciString(coefficient, exponent.Int64())}
	if negative {
		result.Text = "-" + result.Text
	}
	return result
}

// decimalSciString is Decimal.__str__ (to_sci_string) of an unsigned value.
func decimalSciString(digits string, exponent int64) string {
	left := exponent + int64(len(digits))
	dot := int64(1)
	if exponent <= 0 && left > -6 {
		dot = left
	}
	var integer, fraction string
	switch {
	case dot <= 0:
		integer, fraction = "0", "."+strings.Repeat("0", int(-dot))+digits
	case dot >= int64(len(digits)):
		integer = digits + strings.Repeat("0", int(dot-int64(len(digits))))
	default:
		integer, fraction = digits[:dot], "."+digits[dot:]
	}
	if left == dot {
		return integer + fraction
	}
	shift := left - dot
	sign := "+"
	if shift < 0 {
		sign = ""
	}
	return integer + fraction + "E" + sign + strconv.FormatInt(shift, 10)
}

// Insert adds codes for a field keeping the serializer's field order.
func (e *PaymentEntryValidationError) Insert(name string, codes ...string) {
	order := map[string]int{}
	for index, field := range paymentEntryOrder {
		order[field] = index
	}
	at := len(e.Fields)
	for index, field := range e.Fields {
		if field.Name == name {
			e.Fields[index].Codes = append(e.Fields[index].Codes, codes...)
			return
		}
		if order[field.Name] > order[name] && at == len(e.Fields) {
			at = index
		}
	}
	e.Fields = append(e.Fields, PaymentEntryFieldError{})
	copy(e.Fields[at+1:], e.Fields[at:])
	e.Fields[at] = PaymentEntryFieldError{Name: name, Codes: codes}
}

// JSONInteger is a parsed JSON integer.
func JSONInteger(value int) json.Number { return json.Number(strconv.Itoa(value)) }

// PythonNumericKey is the exact value of a bool or finite number (set and
// dict identity: 1 == 1.0 == True).
func PythonNumericKey(value any) (string, bool) {
	number, ok := pythonNumeric(value)
	if !ok {
		return "", false
	}
	return number.RatString(), true
}

// DjangoDateTimeToPython is models.DateTimeField.to_python of a string
// (parse_datetime, naive values in UTC): ok false is the ValidationError.
func DjangoDateTimeToPython(text string) (time.Time, bool) {
	parsed, err := parsePaymentCategoryDjangoDateTime(text)
	if err != nil {
		return time.Time{}, false
	}
	return parsed.UTC(), true
}
