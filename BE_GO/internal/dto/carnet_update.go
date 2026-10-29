package dto

import (
	"encoding/json"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// CarnetUpdateInput is CarnetUpdateSerializer's validated data.
type CarnetUpdateInput struct {
	Title, Description string
	Lessons            int64
	Fee                string
	Public             bool
}

// CarnetUpdateValidationError carries DRF-ordered error codes per field.
type CarnetUpdateValidationError struct{ Fields map[string][]string }

func (e *CarnetUpdateValidationError) Error() string { return "invalid carnet update input" }

func pythonTypeName(value any) string {
	switch v := value.(type) {
	case []any:
		return "list"
	case string:
		return "str"
	case bool:
		return "bool"
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "float"
		}
		return "int"
	case nil:
		return "NoneType"
	}
	return "dict"
}

// ParseCarnetUpdate ports CarnetUpdateSerializer(data=...).is_valid(): every
// field is required; a form body without public validates as false
// (BooleanField.default_empty_html).
func ParseCarnetUpdate(body any, form bool) (CarnetUpdateInput, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return CarnetUpdateInput{}, &CarnetUpdateValidationError{Fields: map[string][]string{
			"non_field_errors": {"dictionary:" + pythonTypeName(body)}}}
	}
	var input CarnetUpdateInput
	errors := map[string][]string{}
	field := func(name string) (any, bool) {
		value, present := object.Lookup(name)
		if !present {
			if name == "public" && form {
				return false, true
			}
			errors[name] = []string{PaymentCategoryValidationRequired}
		}
		return value, present
	}
	for _, text := range []struct {
		name    string
		maximum int
		target  *string
	}{{"title", 255, &input.Title}, {"description", 1024, &input.Description}} {
		if value, present := field(text.name); present {
			parsed, codes := parsePaymentCategoryString(value, text.maximum)
			if len(codes) > 0 {
				errors[text.name] = codes
			}
			*text.target = parsed
		}
	}
	if value, present := field("lessons_number"); present {
		switch {
		case value == nil:
			errors["lessons_number"] = []string{PaymentCategoryValidationNull}
		default:
			number, code := DRFInteger(value)
			switch {
			case code != "":
				errors["lessons_number"] = []string{code}
			case number.Sign() < 1:
				errors["lessons_number"] = []string{"min-value:1"}
			case !number.IsInt64():
				return CarnetUpdateInput{}, ErrDjangoFieldPrep
			default:
				input.Lessons = number.Int64()
			}
		}
	}
	if value, present := field("fee"); present {
		switch {
		case value == nil:
			errors["fee"] = []string{PaymentCategoryValidationNull}
		default:
			fee, code := DRFDecimal(value, 9, 2)
			switch {
			case code != "":
				errors["fee"] = []string{code}
			case strings.HasPrefix(fee, "-") && strings.Trim(fee, "-0.") != "":
				errors["fee"] = []string{"min-value:0.0"}
			default:
				input.Fee = fee
			}
		}
	}
	if value, present := field("public"); present {
		switch {
		case value == nil:
			errors["public"] = []string{PaymentCategoryValidationNull}
		default:
			flag, err := parsePaymentCategoryBoolean(value)
			if err != nil {
				errors["public"] = []string{PaymentCategoryValidationBoolean}
			}
			input.Public = flag
		}
	}
	if len(errors) > 0 {
		return CarnetUpdateInput{}, &CarnetUpdateValidationError{Fields: errors}
	}
	return input, nil
}

// CarnetAddInput is CarnetAddSerializer's validated data; Meta is the parsed
// JSONField value (nil without the key: default=None).
type CarnetAddInput struct {
	CarnetUpdateInput
	Meta any
}

// CarnetAddMetaDict is the serializer's is_valid() override message.
const CarnetAddMetaDict = "carnet-meta-dict"

// ParseCarnetAdd ports CarnetAddSerializer(data=...).is_valid(): the
// CarnetUpdateSerializer field rules without public, plus meta (JSONField,
// null rejected) that, when truthy, must be a dict.
func ParseCarnetAdd(body any) (CarnetAddInput, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		_, err := ParseCarnetUpdate(body, false)
		return CarnetAddInput{}, err
	}
	fields := domain.RequestObject{}
	for _, field := range object {
		if field.Name != "public" {
			fields = append(fields, field)
		}
	}
	input, err := ParseCarnetUpdate(append(fields, domain.RequestField{Name: "public", Value: true}), false)
	errors := map[string][]string{}
	var invalid *CarnetUpdateValidationError
	switch {
	case err == nil:
	case asCarnetValidation(err, &invalid):
		for name, codes := range invalid.Fields {
			errors[name] = codes
		}
	default:
		return CarnetAddInput{}, err
	}
	meta, present := object.Lookup("meta")
	if present && meta == nil {
		errors["meta"] = []string{PaymentCategoryValidationNull}
	}
	if len(errors) > 0 {
		return CarnetAddInput{}, &CarnetUpdateValidationError{Fields: errors}
	}
	if _, dict := meta.(domain.RequestObject); present && PythonTruthy(meta) && !dict {
		return CarnetAddInput{}, &CarnetUpdateValidationError{Fields: map[string][]string{"meta": {CarnetAddMetaDict}}}
	}
	return CarnetAddInput{CarnetUpdateInput: input, Meta: meta}, nil
}

func asCarnetValidation(err error, target **CarnetUpdateValidationError) bool {
	value, ok := err.(*CarnetUpdateValidationError)
	if ok {
		*target = value
	}
	return ok
}
