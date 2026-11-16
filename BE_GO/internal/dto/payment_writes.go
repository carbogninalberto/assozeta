package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"regexp"
	"sort"
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
	// Nested is the error dictionary of a nested serializer (the `user` object).
	Nested []PaymentEntryFieldError
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
	case strings.HasPrefix(code, "unique:"):
		return "unique"
	}
	return "invalid"
}

// PaymentEntryJSONText is a JSONField value of a form body: DRF marks an HTML
// value as JSON text (JSONField.get_value) and json.loads parses it.
type PaymentEntryJSONText struct{ Text string }

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
	// PaymentID is a body primary key: the serializer's UUIDField and its
	// UniqueValidator (the service) validate it.
	PaymentID                      *string
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
	// UserPresent is a valid nested `user` object: the serializer's save() cannot write it.
	UserPresent bool
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
// for the update view). The nested user is validated here; the payment_id
// UniqueValidator is the service's (it reads the manager).
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
		case "payment_id":
			if value == nil {
				fail(PaymentCategoryValidationNull)
			} else if id, ok := drfUUID(value); ok {
				input.PaymentID = &id
			} else {
				fail(PaymentCategoryValidationUUID)
			}
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
		case "user":
			if value == nil {
				fail(PaymentCategoryValidationNull)
				continue
			}
			if nested := paymentEntryUser(value); nested != nil {
				invalid.add(PaymentEntryFieldError{Name: name, Nested: nested})
			} else {
				input.UserPresent = true
			}
		case "course", "meta_payment_categories":
			parsed := value
			if text, ok := value.(PaymentEntryJSONText); ok {
				// JSONField.get_value marked an HTML form value as JSON text.
				tree, valid, _, _ := automationFormTree(text.Text)
				if !valid {
					fail(CourseSubscriptionJSONInvalid)
					continue
				}
				parsed = tree
			}
			if name == "course" {
				input.Course = PaymentEntryJSON{Present: true, Value: parsed}
			} else {
				input.Meta = PaymentEntryJSON{Present: true, Value: parsed}
			}
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

// --- HTML form bodies ---------------------------------------------------------

// paymentEntryFormOption is one field's HTML extraction options: the model's
// null and blank (allow_null/allow_blank) and the serializer field's required.
type paymentEntryFormOption struct{ null, blank, required bool }

// paymentEntryFormOptions are the PaymentEntrySerializer fields whose HTML
// value Field.get_value extracts without an override. A nested serializer
// (<name>.<field> keys), a ListField (getlist) and a JSONField (JSON text) are
// handled separately.
var paymentEntryFormOptions = map[string]paymentEntryFormOption{
	"payment_id": {}, "type": {}, "subject": {}, "amount": {}, "expense": {}, "paid": {},
	"description": {null: true}, "payment_date": {null: true},
	"payment_category": {null: true}, "sport_association": {null: true}, "custom_accounts": {null: true},
	"notes": {null: true, blank: true}, "creation_date": {required: true},
}

// PaymentEntryFormObject converts a form body (urlencoded or multipart) into
// the object ParsePaymentEntry validates: DRF's HTML form extraction field by
// field (Field.get_value, with the overrides a nested serializer, a ListField
// and a JSONField carry). Fields the extraction skips are left out.
//
// refused is true when a JSON field's text (course, meta_payment_categories)
// nests past the ported depth or holds a lone surrogate escape. Django still
// validates every field first (another field's error is a 400); the depth at
// which Python's JSON codec raises depends on the live recursion depth, so that
// band stays a handoff refusal before any write. surrogate reports the other
// case alone: the text parses and the value reaches the save, where auditlog's
// pre_save entry is written before the jsonb column refuses the value (a depth
// past the ported band wins over a surrogate, as json.loads raises first).
func PaymentEntryFormObject(object domain.RequestObject, partial bool) (converted domain.RequestObject, refused, surrogate bool) {
	converted = domain.RequestObject{}
	deep := false
	for _, name := range paymentEntryOrder {
		switch name {
		case "attachments":
			// A read-only declared field: never validated.
			continue
		case "user":
			// Serializer.get_value: html.parse_html_dict(<name>.<field>).
			if child, found := paymentEntryFormChild(object, name); found {
				converted = append(converted, domain.RequestField{Name: name, Value: paymentEntryUserFormObject(child)})
			}
			continue
		case "course", "meta_payment_categories":
			// JSONField.get_value marks a present HTML value as JSON text.
			if value, present := object.Lookup(name); present {
				text, ok := value.(string)
				if !ok {
					if upload, isUpload := value.(domain.RequestUpload); isUpload {
						text = upload.Name
					} else {
						text = PythonStr(value)
					}
				}
				if _, _, rejected, tooDeep := automationFormTree(text); rejected || tooDeep {
					refused = true
					surrogate = surrogate || rejected
					deep = deep || tooDeep
				}
				converted = append(converted, domain.RequestField{Name: name, Value: PaymentEntryJSONText{Text: text}})
			}
			continue
		case "attachments_data":
			// ListField.get_value: QueryDict.getlist, else parse_html_list.
			if values, present := object.List(name); present && len(values) > 0 {
				converted = append(converted, domain.RequestField{Name: name, Value: values})
				continue
			}
			indexed := false
			for _, field := range object {
				if strings.HasPrefix(field.Name, name+"[") {
					indexed = true
					break
				}
			}
			if partial && !indexed {
				continue
			}
			if list := paymentEntryFormList(object, name); list != nil {
				converted = append(converted, domain.RequestField{Name: name, Value: list})
			}
			continue
		}
		option, known := paymentEntryFormOptions[name]
		if !known {
			continue
		}
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if kept, keep := paymentEntryFormValue(value, option); keep {
			converted = append(converted, domain.RequestField{Name: name, Value: kept})
		}
	}
	return converted, refused, surrogate && !deep
}

// paymentEntryFormValue is Field.get_value of a present HTML value: an empty
// input is None when the field allows null (the empty string when it also
// allows blank), the empty string for any other optional blank field, skipped
// for any other optional field, and validated as sent by a required one.
func paymentEntryFormValue(value any, option paymentEntryFormOption) (any, bool) {
	text, isText := value.(string)
	if !isText || text != "" {
		return value, true
	}
	switch {
	case option.null:
		if option.blank {
			return "", true
		}
		return nil, true
	case !option.required:
		if option.blank {
			return "", true
		}
		return nil, false
	}
	return value, true
}

// paymentEntryFormChild is html.parse_html_dict(dictionary, prefix=<name>): the
// "<name>.<field>" keys with the prefix removed (at least one character must
// follow the dot).
func paymentEntryFormChild(object domain.RequestObject, name string) (domain.RequestObject, bool) {
	prefix := name + "."
	child := domain.RequestObject{}
	for _, field := range object {
		if len(field.Name) > len(prefix) && strings.HasPrefix(field.Name, prefix) {
			child = append(child, domain.RequestField{Name: strings.TrimPrefix(field.Name, prefix),
				Value: field.Value, All: field.All})
		}
	}
	return child, len(child) > 0
}

// paymentEntryUserFormObject applies Field.get_value to the nested user's own
// fields, so ParsePaymentEntry's JSON path validates the same values.
func paymentEntryUserFormObject(child domain.RequestObject) domain.RequestObject {
	out := domain.RequestObject{}
	for _, field := range paymentEntryUserFields {
		value, present := child.Lookup(field.Name)
		if !present {
			continue
		}
		// Every UserSerializer field is optional.
		if converted, keep := paymentEntryFormValue(value, paymentEntryFormOption{null: field.Null, blank: field.Blank}); keep {
			out = append(out, domain.RequestField{Name: field.Name, Value: converted})
		}
	}
	if value, present := child.Lookup("role"); present {
		if converted, keep := paymentEntryFormValue(value, paymentEntryFormOption{}); keep {
			out = append(out, domain.RequestField{Name: "role", Value: converted})
		}
	}
	return out
}

// paymentEntryFormList is html.parse_html_list(dictionary, prefix=<name>): the
// "<name>[<index>]" values and "<name>[<index>][<key>]" dictionaries, in index
// order. A dictionary of one index holds its keys' last values.
func paymentEntryFormList(object domain.RequestObject, name string) []any {
	type item struct {
		value any
		keys  []string
		dict  map[string]any
	}
	items := map[int]*item{}
	var order []int
	prefix := name + "["
	for _, field := range object {
		if !strings.HasPrefix(field.Name, prefix) {
			continue
		}
		rest := strings.TrimPrefix(field.Name, prefix)
		end := strings.IndexByte(rest, ']')
		if end < 0 {
			continue
		}
		index, err := strconv.Atoi(rest[:end])
		if err != nil || index < 0 || !allASCIIDigits(rest[:end]) {
			continue
		}
		key := rest[end+1:]
		current, known := items[index]
		if !known {
			current = &item{}
			items[index] = current
			order = append(order, index)
		}
		switch {
		case key == "":
			// A scalar replaces any dictionary built for the index.
			current.value, current.keys, current.dict = field.Value, nil, nil
		case current.dict == nil:
			current.value = nil
			current.dict = map[string]any{key: field.Value}
			current.keys = []string{key}
		default:
			if _, seen := current.dict[key]; !seen {
				current.keys = append(current.keys, key)
			}
			current.dict[key] = field.Value
		}
	}
	if len(items) == 0 {
		return nil
	}
	sort.Ints(order)
	list := make([]any, 0, len(order))
	for _, index := range order {
		current := items[index]
		if current.dict == nil {
			list = append(list, current.value)
			continue
		}
		entry := make(domain.RequestObject, 0, len(current.keys))
		for _, key := range current.keys {
			entry = append(entry, domain.RequestField{Name: key, Value: current.dict[key]})
		}
		list = append(list, entry)
	}
	return list
}

// allASCIIDigits is strconv.Atoi's decimal grammar in Python's re [0-9]+ (the
// list index of an HTML form key).
func allASCIIDigits(text string) bool {
	if text == "" {
		return false
	}
	for _, r := range text {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// paymentEntryUserFields is UserSerializer's Meta.fields with the model
// field options DRF derives its validators from.
var paymentEntryUserFields = []SupplierField{
	{Name: "first_name", Max: 150, Blank: true},
	{Name: "last_name", Max: 150, Blank: true},
	{Name: "email", Max: 254, Blank: true, Email: true},
	{Name: "phone", Max: 20, Null: true, Blank: true},
	{Name: "username"},
}

// paymentEntryUser validates the nested UserSerializer (every field optional):
// nil when the object is valid, else its error dictionary.
func paymentEntryUser(value any) []PaymentEntryFieldError {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return []PaymentEntryFieldError{{Name: "non_field_errors", Codes: []string{"dictionary:" + pythonTypeName(value)}}}
	}
	var errors []PaymentEntryFieldError
	for _, field := range paymentEntryUserFields {
		item, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		if _, codes := modelTextValue(field, item, false); len(codes) > 0 {
			errors = append(errors, PaymentEntryFieldError{Name: field.Name, Codes: codes})
		}
	}
	if item, present := object.Lookup("role"); present {
		if _, code := paymentEntryChoice(item, []string{"1", "2", "3"}); code != "" {
			errors = append(errors, PaymentEntryFieldError{Name: "role", Codes: []string{code}})
		}
	}
	return errors
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
