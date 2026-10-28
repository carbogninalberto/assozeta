package dto

import (
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// BalanceAccountWrite is CustomAccountSerializer's validated data; nil
// pointers are absent fields.
type BalanceAccountWrite struct {
	ID                *string
	Enabled, Editable *bool
	Name, AccountCode *string
	InitialBalance    *string
	AccountType       *int
}

// BalanceAccountUnique is the custom_account_id UniqueValidator message code.
const BalanceAccountUnique = "unique-custom-account"

// ParseBalanceAccountWrite ports CustomAccountSerializer field validation
// (uniqueness is checked by the service). A form body without enabled or
// editable sends False unless partial (BooleanField.default_empty_html).
func ParseBalanceAccountWrite(body any, partial, form bool) (BalanceAccountWrite, map[string]any) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return BalanceAccountWrite{}, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}
	}
	var write BalanceAccountWrite
	errors := map[string]any{}
	if value, present := object.Lookup("custom_account_id"); present {
		if value == nil {
			errors["custom_account_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := supplierUUID(value); ok {
			write.ID = &id
		} else {
			errors["custom_account_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	for _, field := range []struct {
		name   string
		target **bool
	}{{"enabled", &write.Enabled}, {"editable", &write.Editable}} {
		value, present := object.Lookup(field.name)
		if !present {
			if form && !partial {
				flag := false
				*field.target = &flag
			}
			continue
		}
		if value == nil {
			errors[field.name] = []string{PaymentCategoryValidationNull}
			continue
		}
		flag, err := parsePaymentCategoryBoolean(value)
		if err != nil {
			errors[field.name] = []string{PaymentCategoryValidationBoolean}
			continue
		}
		*field.target = &flag
	}
	for _, field := range []struct {
		name   string
		target **string
	}{{"name", &write.Name}, {"account_code", &write.AccountCode}} {
		value, present := object.Lookup(field.name)
		if !present {
			if !partial {
				errors[field.name] = []string{PaymentCategoryValidationRequired}
			}
			continue
		}
		text, codes := parsePaymentCategoryString(value, 100)
		if len(codes) > 0 {
			errors[field.name] = codes
			continue
		}
		*field.target = &text
	}
	if value, present := object.Lookup("initial_balance"); !present {
		if !partial {
			errors["initial_balance"] = []string{PaymentCategoryValidationRequired}
		}
	} else if value == nil {
		errors["initial_balance"] = []string{PaymentCategoryValidationNull}
	} else if amount, code := DRFDecimal(value, 10, 2); code != "" {
		errors["initial_balance"] = []string{code}
	} else {
		write.InitialBalance = &amount
	}
	if value, present := object.Lookup("account_type"); present {
		if value == nil {
			errors["account_type"] = []string{PaymentCategoryValidationNull}
		} else {
			// ChoiceField: the str() of the value against the choice keys.
			raw := PythonStr(value)
			switch raw {
			case "1", "2", "3":
				kind := int(raw[0] - '0')
				write.AccountType = &kind
			default:
				errors["account_type"] = []string{"choice:" + raw}
			}
		}
	}
	if len(errors) > 0 {
		return write, errors
	}
	return write, nil
}
