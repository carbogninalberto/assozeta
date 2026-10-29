package dto

import (
	"encoding/json"
	"math/big"
	"net/netip"
	"regexp"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// SupplierField describes one SupplierSerializer (ModelSerializer, fields
// '__all__') text field: model CharField/TextField/EmailField options.
type SupplierField struct {
	Name      string
	Max       int // 0: no maximum (TextField)
	Null      bool
	Blank     bool
	Required  bool
	Email     bool
	DRFFields bool
}

// SupplierTextFields are the model's text fields in declaration order.
var SupplierTextFields = []SupplierField{
	{Name: "name", Max: 255, Required: true},
	{Name: "address", Max: 255, Null: true, Blank: true},
	{Name: "tax_code", Max: 30, Null: true, Blank: true},
	{Name: "vat_number", Max: 30, Null: true, Blank: true},
	{Name: "type", Max: 30, Null: true},
	{Name: "email", Max: 254, Null: true, Blank: true, Email: true},
	{Name: "phone_number", Max: 20, Null: true, Blank: true},
	{Name: "city", Max: 100, Null: true, Blank: true},
	{Name: "cap", Max: 10, Null: true, Blank: true},
	{Name: "state", Max: 50, Null: true, Blank: true},
	{Name: "country", Max: 50, Null: true, Blank: true},
	{Name: "nationality", Max: 100, Null: true, Blank: true},
	{Name: "note", Null: true, Blank: true},
}

// SupplierFieldOrder is the serializer's field order (model declaration).
var SupplierFieldOrder = []string{"supplier_id", "name", "address", "tax_code", "sport_association", "vat_number", "type", "email",
	"phone_number", "city", "cap", "state", "country", "nationality", "note", "group", "deleted"}

// SupplierWrite is the validated data before the related-field lookups:
// Text holds present text fields (nil for None); IDs are canonical UUIDs.
type SupplierWrite struct {
	SupplierID         *string
	Text               map[string]*string
	AssociationPresent bool
	AssociationID      *string
	GroupPresent       bool
	GroupID            *string
	DeletedPresent     bool
	Deleted            bool
}

// SupplierValidationError keeps DRF's field order; codes are rendered by the
// handler (messages and the repr of ErrorDetail codes).
type SupplierValidationError struct {
	Fields []SupplierFieldError
}

type SupplierFieldError struct {
	Name  string
	Codes []string
}

func (e *SupplierValidationError) Error() string { return "invalid supplier input" }

// Add appends codes for a field keeping the serializer's field order.
func (e *SupplierValidationError) Add(name string, codes ...string) {
	for index := range e.Fields {
		if e.Fields[index].Name == name {
			e.Fields[index].Codes = append(e.Fields[index].Codes, codes...)
			return
		}
	}
	e.Fields = append(e.Fields, SupplierFieldError{Name: name, Codes: codes})
	order := map[string]int{"non_field_errors": -1}
	for index, field := range SupplierFieldOrder {
		order[field] = index
	}
	for i := len(e.Fields) - 1; i > 0 && order[e.Fields[i].Name] < order[e.Fields[i-1].Name]; i-- {
		e.Fields[i], e.Fields[i-1] = e.Fields[i-1], e.Fields[i]
	}
}

// SupplierDRFCode is the ErrorDetail code of a validation code.
func SupplierDRFCode(code string) string {
	switch {
	case code == PaymentCategoryValidationRequired, code == PaymentCategoryValidationNull, code == PaymentCategoryValidationBlank:
		return code
	case strings.HasPrefix(code, "max:"):
		return "max_length"
	case code == "null-character":
		return "null_characters_not_allowed"
	case strings.HasPrefix(code, "surrogate:"):
		return "surrogate_characters_not_allowed"
	case code == SupplierValidationUnique:
		return "unique"
	case strings.HasPrefix(code, "related-does-not-exist:"):
		return "does_not_exist"
	case strings.HasPrefix(code, "related-incorrect-type:"):
		return "incorrect_type"
	}
	// related-invalid-uuid, email and the to_internal_value failures.
	return "invalid"
}

const (
	SupplierValidationEmail  = "supplier-email"
	SupplierValidationUnique = "supplier-unique"
)

var supplierUserRegexp = regexp.MustCompile(`(?i)(^[-!#$%&'*+/=?^_` + "`" + `{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_` + "`" + `{}|~0-9A-Z]+)*\z` +
	`|^"([\x01-\x08\x0b\x0c\x0e-\x1f!#-\[\]-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*"\z)`)

func supplierLabelRune(r rune, digits bool) bool {
	return r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= 0x00a1 && r <= 0xffff || digits && r >= '0' && r <= '9'
}

// supplierLabel is hostname_re / domain_re: 1..63 label characters (and '-'
// inside), not starting or ending with '-'.
func supplierLabel(label string, digits bool) bool {
	runes := []rune(label)
	if len(runes) < 1 || len(runes) > 63 || runes[0] == '-' || runes[len(runes)-1] == '-' {
		return false
	}
	for _, r := range runes {
		if r != '-' && !supplierLabelRune(r, digits) {
			return false
		}
	}
	return true
}

// supplierDomain is EmailValidator.validate_domain_part for Django 5.2.
func supplierDomain(domain string) bool {
	if domain == "localhost" {
		return true
	}
	labels := strings.Split(domain, ".")
	if len(labels) >= 2 {
		valid := true
		for _, label := range labels[:len(labels)-1] {
			valid = valid && supplierLabel(label, true)
		}
		tld := labels[len(labels)-1]
		lower := strings.ToLower(tld)
		punycode := strings.HasPrefix(lower, "xn--") && len(lower) >= 5 && len(lower) <= 63 &&
			strings.Trim(lower[4:], "abcdefghijklmnopqrstuvwxyz0123456789") == ""
		letters := len([]rune(tld)) >= 2 && supplierLabel(tld, false)
		if valid && (letters || punycode) {
			return true
		}
	}
	if strings.HasPrefix(domain, "[") && strings.HasSuffix(domain, "]") {
		literal := domain[1 : len(domain)-1]
		if literal == "" || strings.Trim(strings.ToUpper(literal), "ABCDEF0123456789:.") != "" {
			return false
		}
		address, err := netip.ParseAddr(literal)
		return err == nil && address.Zone() == ""
	}
	return false
}

// SupplierEmail is Django's EmailValidator (5.2).
func SupplierEmail(value string) bool {
	if value == "" || !strings.Contains(value, "@") || len([]rune(value)) > 320 {
		return false
	}
	at := strings.LastIndex(value, "@")
	return supplierUserRegexp.MatchString(value[:at]) && supplierDomain(value[at+1:])
}

// supplierUUID is serializers.UUIDField.to_internal_value.
func supplierUUID(value any) (string, bool) {
	switch v := value.(type) {
	case string:
		text := strings.ReplaceAll(strings.ReplaceAll(v, "urn:", ""), "uuid:", "")
		text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
		if len(text) != 32 || strings.Trim(strings.ToLower(text), "0123456789abcdef") != "" {
			return "", false
		}
		text = strings.ToLower(text)
		return text[:8] + "-" + text[8:12] + "-" + text[12:16] + "-" + text[16:20] + "-" + text[20:], true
	case json.Number:
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return "", false
		}
		var raw [16]byte
		number.FillBytes(raw[:])
		const hex = "0123456789abcdef"
		out := make([]byte, 0, 36)
		for index, b := range raw {
			if index == 4 || index == 6 || index == 8 || index == 10 {
				out = append(out, '-')
			}
			out = append(out, hex[b>>4], hex[b&15])
		}
		return string(out), true
	}
	return "", false
}

// supplierRelated is PrimaryKeyRelatedField's pk_field-less to_internal_value
// before the queryset lookup: lists and dicts are incorrect types; other
// values are looked up (a value the UUID field rejects does not exist).
func supplierRelated(value any) (id string, code string) {
	switch value.(type) {
	case []any, domain.RequestObject:
		return "", "related-incorrect-type:" + pythonTypeName(value)
	case bool:
		return "", "related-incorrect-type:bool"
	}
	if parsed, ok := supplierUUID(value); ok {
		return parsed, ""
	}
	if text, ok := value.(string); ok {
		// The model UUIDField's django ValidationError, reported as is.
		return "", "related-invalid-uuid:" + text
	}
	return "", "related-incorrect-type:" + pythonTypeName(value)
}

// modelTextValue validates one present value of a ModelSerializer text field
// (the model CharField/TextField options); nil text is None.
func modelTextValue(field SupplierField, value any, form bool) (*string, []string) {
	if form && value == "" && field.Null && !field.Blank {
		// get_value: an empty HTML input of a nullable, non-blank field is None.
		value = nil
	}
	if value == nil {
		if !field.Null {
			return nil, []string{PaymentCategoryValidationNull}
		}
		return nil, nil
	}
	maximum := field.Max
	if maximum == 0 {
		maximum = int(^uint(0) >> 1)
	}
	text, codes := parsePaymentCategoryString(value, maximum)
	if len(codes) == 1 && codes[0] == PaymentCategoryValidationBlank && field.Blank {
		empty := ""
		return &empty, nil
	}
	// run_validators collects every validator error; EmailValidator runs
	// after the length and character validators on any string value.
	if field.Email && !(len(codes) == 1 && codes[0] == PaymentCategoryValidationString) && !SupplierEmail(text) {
		codes = append(codes, SupplierValidationEmail)
	}
	if len(codes) > 0 {
		return nil, codes
	}
	return &text, nil
}

// ParseSupplierWrite ports SupplierSerializer(data=..., partial=...) field
// validation; related existence, tenant scope and uniqueness are checked by
// the service.
func ParseSupplierWrite(body any, partial, form bool) (SupplierWrite, *SupplierValidationError, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		invalid := &SupplierValidationError{}
		invalid.Add("non_field_errors", "dictionary:"+pythonTypeName(body))
		return SupplierWrite{}, invalid, nil
	}
	write := SupplierWrite{Text: map[string]*string{}}
	invalid := &SupplierValidationError{}
	if value, present := object.Lookup("supplier_id"); present {
		if value == nil {
			invalid.Add("supplier_id", PaymentCategoryValidationNull)
		} else if id, ok := supplierUUID(value); ok {
			write.SupplierID = &id
		} else {
			invalid.Add("supplier_id", PaymentCategoryValidationUUID)
		}
	}
	for _, field := range SupplierTextFields {
		value, present := object.Lookup(field.Name)
		if !present {
			if field.Required && !partial {
				invalid.Add(field.Name, PaymentCategoryValidationRequired)
			}
			continue
		}
		text, codes := modelTextValue(field, value, form)
		if len(codes) > 0 {
			invalid.Add(field.Name, codes...)
			continue
		}
		write.Text[field.Name] = text
	}
	related := func(name string, present *bool, target **string) {
		value, found := object.Lookup(name)
		if !found {
			return
		}
		*present = true
		if value == nil {
			return
		}
		id, code := supplierRelated(value)
		if code != "" {
			invalid.Add(name, code)
			return
		}
		*target = &id
	}
	related("sport_association", &write.AssociationPresent, &write.AssociationID)
	related("group", &write.GroupPresent, &write.GroupID)
	if value, present := object.Lookup("deleted"); present {
		if value == nil {
			invalid.Add("deleted", PaymentCategoryValidationNull)
		} else if flag, err := parsePaymentCategoryBoolean(value); err != nil {
			invalid.Add("deleted", PaymentCategoryValidationBoolean)
		} else {
			write.DeletedPresent, write.Deleted = true, flag
		}
	}
	if len(invalid.Fields) > 0 {
		return write, invalid, nil
	}
	return write, nil, nil
}
