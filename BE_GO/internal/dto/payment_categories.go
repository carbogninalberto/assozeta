package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type PaymentCategory struct {
	ID            string              `json:"payment_category_id"`
	Name          string              `json:"name"`
	CreationDate  string              `json:"creation_date"`
	AssociationID *string             `json:"sport_association"`
	Expense       bool                `json:"expense"`
	TaxDeductible bool                `json:"tax_deductible"`
	Archived      bool                `json:"archived"`
	Type          uint16              `json:"type"`
	VAT           *PaymentCategoryVAT `json:"vat_management"`
	Deleted       bool                `json:"deleted"`
}

type PaymentCategoryVAT struct {
	ID        string `json:"vat_management_id"`
	VAT       uint16 `json:"vat"`
	Deduction uint16 `json:"deduction"`
}

func PaymentCategoryFromState(state domain.PaymentCategoryState) PaymentCategory {
	result := PaymentCategory{
		ID: state.ID, Name: state.Name, CreationDate: paymentCategoryResponseTime(state.CreationDate), AssociationID: state.AssociationID,
		Expense: state.Expense, TaxDeductible: state.TaxDeductible, Archived: state.Archived,
		Type: state.Type, Deleted: state.Deleted,
	}
	if state.VAT != nil {
		result.VAT = &PaymentCategoryVAT{ID: state.VAT.ID, VAT: state.VAT.VAT, Deduction: state.VAT.Deduction}
	}
	return result
}

func paymentCategoryResponseTime(value time.Time) string {
	value = value.UTC()
	base := value.Format("2006-01-02T15:04:05")
	if microsecond := value.Nanosecond() / 1000; microsecond != 0 {
		return base + fmt.Sprintf(".%06dZ", microsecond)
	}
	return base + "Z"
}

func (value PaymentCategory) JSONMap() map[string]any {
	var vat any
	if value.VAT != nil {
		vat = map[string]any{"vat_management_id": value.VAT.ID, "vat": value.VAT.VAT, "deduction": value.VAT.Deduction}
	}
	return map[string]any{
		"payment_category_id": value.ID, "name": value.Name, "creation_date": value.CreationDate,
		"sport_association": value.AssociationID, "expense": value.Expense,
		"tax_deductible": value.TaxDeductible, "archived": value.Archived, "type": value.Type,
		"vat_management": vat, "deleted": value.Deleted,
	}
}

type PaymentCategoryValidationError struct {
	Errors map[string]any
}

func (e *PaymentCategoryValidationError) Error() string { return "invalid payment category input" }

type PaymentCategoryBodyError struct{ Kind string }

func (e *PaymentCategoryBodyError) Error() string { return "invalid payment category body: " + e.Kind }

type PaymentCategoryNestedValidationError struct{ Errors map[string]any }

func (e *PaymentCategoryNestedValidationError) Error() string {
	return "invalid nested payment category input"
}

const (
	PaymentCategoryValidationRequired         = "required"
	PaymentCategoryValidationNull             = "null"
	PaymentCategoryValidationBlank            = "blank"
	PaymentCategoryValidationUUID             = "uuid"
	PaymentCategoryValidationString           = "string"
	PaymentCategoryValidationDateTime         = "datetime"
	PaymentCategoryValidationDateTimeOverflow = "datetime-overflow"
	PaymentCategoryValidationBoolean          = "boolean"
	PaymentCategoryValidationDict             = "dictionary"
	PaymentCategoryValidationUniqueVAT        = "unique-vat"
	PaymentCategoryValidationUniqueID         = "unique-category"
)

type PaymentCategoryVATWrite struct {
	Present bool
	Null    bool
	Value   PaymentCategoryVATInput
}

type PaymentCategoryVATInput struct {
	ID        *string
	VAT       *uint16
	Deduction *uint16
	NonEmpty  bool
}

type PaymentCategoryWrite struct {
	// Retain scalar errors so services can aggregate database-backed validators
	// without applying any writes. Valid fields remain available for those checks.
	ValidationErrors                                                        map[string]any
	CategoryIDPresent, NamePresent, CreationDatePresent, AssociationPresent bool
	ExpensePresent, TaxDeductiblePresent, ArchivedPresent, TypePresent      bool
	DeletedPresent                                                          bool

	CategoryID    *string
	Name          *string
	CreationDate  *time.Time
	AssociationID *string
	Expense       *bool
	TaxDeductible *bool
	Archived      *bool
	Type          *uint16
	Deleted       *bool
	VAT           PaymentCategoryVATWrite
}

// ParsePaymentCategoryWrite mirrors the writable model serializer while
// retaining explicit presence. Association ownership and protected lifecycle
// fields remain service policy, not transport defaults.
func ParsePaymentCategoryWrite(body any, partial bool) (PaymentCategoryWrite, error) {
	empty := PaymentCategoryWrite{}
	if body == nil {
		if partial {
			validation := map[string]any{"non_field_errors": []string{"no-data"}}
			return PaymentCategoryWrite{ValidationErrors: validation}, &PaymentCategoryValidationError{Errors: validation}
		}
		return empty, &PaymentCategoryBodyError{Kind: "null"}
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		if partial {
			validation := map[string]any{"non_field_errors": []string{"dictionary:" + paymentCategoryInputType(body)}}
			return PaymentCategoryWrite{ValidationErrors: validation}, &PaymentCategoryValidationError{Errors: validation}
		}
		return empty, &PaymentCategoryBodyError{Kind: "non-object"}
	}
	validation := &PaymentCategoryValidationError{Errors: map[string]any{}}
	add := func(field, message string) {
		current, _ := validation.Errors[field].([]string)
		validation.Errors[field] = append(current, message)
	}
	result := PaymentCategoryWrite{}

	if value, present := object.Lookup("payment_category_id"); present {
		result.CategoryIDPresent = true
		if value == nil {
			add("payment_category_id", PaymentCategoryValidationNull)
		} else if id, err := parsePaymentCategoryUUID(value); err != nil {
			add("payment_category_id", PaymentCategoryValidationUUID)
		} else {
			result.CategoryID = &id
		}
	}
	if value, present := object.Lookup("name"); present {
		result.NamePresent = true
		if text, messages := parsePaymentCategoryString(value, 255); len(messages) > 0 {
			for _, message := range messages {
				add("name", message)
			}
		} else {
			result.Name = &text
		}
	} else if !partial {
		add("name", PaymentCategoryValidationRequired)
	}
	if value, present := object.Lookup("creation_date"); present {
		result.CreationDatePresent = true
		if value == nil {
			add("creation_date", PaymentCategoryValidationNull)
		} else if text, ok := value.(string); !ok {
			add("creation_date", PaymentCategoryValidationDateTime)
		} else if parsed, err := parsePaymentCategoryDate(text); err != nil {
			if errors.Is(err, errPaymentCategoryDateTimeOverflow) {
				add("creation_date", PaymentCategoryValidationDateTimeOverflow)
			} else {
				add("creation_date", PaymentCategoryValidationDateTime)
			}
		} else {
			result.CreationDate = &parsed
		}
	}
	if value, present := object.Lookup("sport_association"); present {
		result.AssociationPresent = true
		// The legacy CREATE view replaces initial_data after the serializer is
		// constructed and before validation, so a client value never wins.
		if partial && value != nil {
			id, err := parsePaymentCategoryUUID(value)
			if err != nil {
				add("sport_association", PaymentCategoryValidationUUID)
			} else {
				result.AssociationID = &id
			}
		}
	}
	for name, destination := range map[string]**bool{
		"expense": &result.Expense, "tax_deductible": &result.TaxDeductible,
		"archived": &result.Archived, "deleted": &result.Deleted,
	} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		switch name {
		case "expense":
			result.ExpensePresent = true
		case "tax_deductible":
			result.TaxDeductiblePresent = true
		case "archived":
			result.ArchivedPresent = true
		case "deleted":
			result.DeletedPresent = true
		}
		parsed, err := parsePaymentCategoryBoolean(value)
		if value == nil {
			add(name, PaymentCategoryValidationNull)
		} else if err != nil {
			add(name, PaymentCategoryValidationBoolean)
		} else {
			*destination = &parsed
		}
	}
	if value, present := object.Lookup("type"); present {
		result.TypePresent = true
		parsed, err := parsePaymentCategoryPositiveSmall(value)
		if value == nil {
			add("type", PaymentCategoryValidationNull)
		} else if err != nil || parsed != 1 && parsed != 2 {
			add("type", "choice:"+paymentCategoryRaw(value))
		} else {
			result.Type = &parsed
		}
	}
	if value, present := object.Lookup("vat_management"); present {
		result.VAT.Present = true
		if value == nil {
			result.VAT.Null = true
			validation.Errors["vat_management"] = []string{PaymentCategoryValidationNull}
		} else {
			nested, ok := value.(domain.RequestObject)
			if !ok {
				validation.Errors["vat_management"] = map[string]any{"non_field_errors": []string{"dictionary:" + paymentCategoryInputType(value)}}
			} else if input, err := parsePaymentCategoryVAT(nested); err != nil {
				result.VAT.Value = input
				var nestedValidation *PaymentCategoryNestedValidationError
				if errors.As(err, &nestedValidation) {
					validation.Errors["vat_management"] = nestedValidation.Errors
				} else {
					validation.Errors["vat_management"] = []string{PaymentCategoryValidationDict}
				}
			} else {
				result.VAT.Value = input
			}
		}
	} else if !partial {
		validation.Errors["vat_management"] = []string{PaymentCategoryValidationRequired}
	}
	if len(validation.Errors) > 0 {
		result.ValidationErrors = validation.Errors
		return result, validation
	}
	return result, nil
}

func parsePaymentCategoryVAT(object domain.RequestObject) (PaymentCategoryVATInput, error) {
	result := PaymentCategoryVATInput{}
	validation := &PaymentCategoryNestedValidationError{Errors: map[string]any{}}
	add := func(field, message string) {
		current, _ := validation.Errors[field].([]string)
		validation.Errors[field] = append(current, message)
	}
	if value, present := object.Lookup("vat_management_id"); present {
		result.NonEmpty = true
		id, err := parsePaymentCategoryUUID(value)
		if value == nil {
			add("vat_management_id", PaymentCategoryValidationNull)
		} else if err != nil {
			add("vat_management_id", PaymentCategoryValidationUUID)
		} else {
			result.ID = &id
		}
	}
	if value, present := object.Lookup("vat"); present {
		result.NonEmpty = true
		parsed, err := parsePaymentCategoryPositiveSmall(value)
		if value == nil {
			add("vat", PaymentCategoryValidationNull)
		} else if err != nil || parsed > 16 {
			add("vat", "choice:"+paymentCategoryRaw(value))
		} else {
			result.VAT = &parsed
		}
	} else {
		zero := uint16(0)
		result.VAT = &zero
	}
	if value, present := object.Lookup("deduction"); present {
		result.NonEmpty = true
		parsed, err := parsePaymentCategoryPositiveSmall(value)
		if value == nil {
			add("deduction", PaymentCategoryValidationNull)
		} else if err != nil || parsed > 2 {
			add("deduction", "choice:"+paymentCategoryRaw(value))
		} else {
			result.Deduction = &parsed
		}
	} else {
		zero := uint16(0)
		result.Deduction = &zero
	}
	if len(validation.Errors) > 0 {
		return result, validation
	}
	return result, nil
}

func parsePaymentCategoryString(value any, maximum int) (string, []string) {
	if value == nil {
		return "", []string{PaymentCategoryValidationNull}
	}
	if number, ok := value.(json.Number); ok {
		raw := number.String()
		if strings.ContainsAny(raw, ".eE") {
			number, err := strconv.ParseFloat(raw, 64)
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return "", []string{PaymentCategoryValidationString}
			}
			if math.IsInf(number, 1) {
				raw = "inf"
			} else if math.IsInf(number, -1) {
				raw = "-inf"
			} else if absolute := math.Abs(number); absolute != 0 && (absolute < 1e-4 || absolute >= 1e16) {
				raw = strconv.FormatFloat(number, 'e', -1, 64)
			} else {
				raw = strconv.FormatFloat(number, 'f', -1, 64)
				if !strings.Contains(raw, ".") {
					raw += ".0"
				}
			}
		} else if raw == "-0" {
			raw = "0"
		}
		value = raw
	}
	text, ok := value.(string)
	if !ok {
		return "", []string{PaymentCategoryValidationString}
	}
	text = strings.TrimFunc(text, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	if text == "" {
		return "", []string{PaymentCategoryValidationBlank}
	}
	length, surrogate := 0, ""
	for offset := 0; offset < len(text); {
		if offset+2 < len(text) && text[offset] == 0xed && text[offset+1] >= 0xa0 && text[offset+1] <= 0xbf && text[offset+2] >= 0x80 && text[offset+2] <= 0xbf {
			code := rune(text[offset]&0x0f)<<12 | rune(text[offset+1]&0x3f)<<6 | rune(text[offset+2]&0x3f)
			if surrogate == "" {
				surrogate = fmt.Sprintf("surrogate:%X", code)
			}
			offset += 3
		} else {
			_, size := utf8.DecodeRuneInString(text[offset:])
			offset += size
		}
		length++
	}
	var messages []string
	if length > maximum {
		messages = append(messages, fmt.Sprintf("max:%d", maximum))
	}
	if strings.ContainsRune(text, 0) {
		messages = append(messages, "null-character")
	}
	if surrogate != "" {
		messages = append(messages, surrogate)
	}
	return text, messages
}

func parsePaymentCategoryUUID(value any) (string, error) {
	switch value := value.(type) {
	case string:
		parsed, err := uuid.Parse(value)
		if err != nil {
			return "", domain.ErrPaymentCategoryInvalidID
		}
		return parsed.String(), nil
	case json.Number:
		raw := value.String()
		if strings.ContainsAny(raw, ".eE") || len(raw) > 39 {
			return "", domain.ErrPaymentCategoryInvalidID
		}
		number, ok := new(big.Int).SetString(raw, 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return "", domain.ErrPaymentCategoryInvalidID
		}
		var id uuid.UUID
		number.FillBytes(id[:])
		return id.String(), nil
	case bool:
		if value {
			return "00000000-0000-0000-0000-000000000001", nil
		}
		return "00000000-0000-0000-0000-000000000000", nil
	default:
		return "", domain.ErrPaymentCategoryInvalidID
	}
}

func parsePaymentCategoryDate(value string) (time.Time, error) {
	return parsePaymentCategoryDjangoDateTime(value)
}

func paymentCategoryInputType(value any) string {
	switch value := value.(type) {
	case []any:
		return "list"
	case string:
		return "str"
	case bool:
		return "bool"
	case json.Number:
		if strings.ContainsAny(value.String(), ".eE") {
			return "float"
		}
		return "int"
	default:
		return "NoneType"
	}
}

func parsePaymentCategoryBoolean(value any) (bool, error) {
	switch value := value.(type) {
	case bool:
		return value, nil
	case string:
		switch strings.ToLower(value) {
		case "t", "y", "yes", "true", "on", "1":
			return true, nil
		case "f", "n", "no", "false", "off", "0":
			return false, nil
		}
	case json.Number:
		number, err := strconv.ParseFloat(string(value), 64)
		if err == nil && number == 0 || err == nil && number == 1 {
			return number == 1, nil
		}
	}
	return false, errors.New("invalid boolean")
}

func parsePaymentCategoryPositiveSmall(value any) (uint16, error) {
	var raw string
	switch value := value.(type) {
	case json.Number:
		raw = value.String()
	case string:
		raw = value
	default:
		return 0, errors.New("invalid positive small integer")
	}
	number, err := strconv.ParseInt(raw, 10, 16)
	if err != nil || number < 0 || strconv.FormatInt(number, 10) != raw {
		return 0, errors.New("invalid positive small integer")
	}
	return uint16(number), nil
}

func paymentCategoryRaw(value any) string {
	switch value := value.(type) {
	case nil:
		return "None"
	case string:
		return value
	case json.Number:
		return value.String()
	case bool:
		if value {
			return "True"
		}
		return "False"
	default:
		return fmt.Sprint(value)
	}
}
