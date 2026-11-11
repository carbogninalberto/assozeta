package dto

import (
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ModuleCapPattern is the code of the address_cap RegexValidator.
const ModuleCapPattern = "module-cap"

const (
	nestedText = iota
	nestedCap
	nestedDecimal
	nestedBool
	nestedJSON
	nestedUUID
)

type moduleNestedField struct {
	name        string
	kind        int
	max         int
	null, blank bool
}

// moduleNestedFields are the writable fields of SportAssociationSerializer
// (Meta.fields order; reviewed and groups are read-only method fields) with
// the attributes of the SportAssociation columns they come from.
var moduleNestedFields = []moduleNestedField{
	{"denomination", nestedText, 150, false, false}, {"tax_code", nestedText, 11, false, false},
	{"address", nestedText, 150, true, false}, {"address_cap", nestedCap, 5, true, false}, {"address_city", nestedText, 150, true, false},
	{"regulation", nestedText, 0, true, false}, {"demand", nestedText, 0, true, false}, {"logo", nestedText, 0, true, false},
	{"subscription_fee", nestedDecimal, 0, false, false}, {"multiple_subscription_fee", nestedBool, 0, false, false},
	{"subscription_fee_plans", nestedJSON, 0, false, false}, {"membership_fee", nestedDecimal, 0, true, false},
	{"multiple_membership_fee", nestedBool, 0, false, false}, {"membership_fee_plans", nestedJSON, 0, false, false},
	{"custom_subscription_form_data", nestedJSON, 0, true, false}, {"document_header", nestedText, 0, true, false},
	{"invoice_footer", nestedText, 0, true, false}, {"enable_quotes_management", nestedBool, 0, false, false},
	{"configuration", nestedJSON, 0, false, false}, {"affiliate_code", nestedText, 8, true, false},
	{"sport_association_id", nestedUUID, 0, false, false}, {"additional_sections", nestedJSON, 0, false, false},
	{"president_signature", nestedText, 0, true, false}, {"stamp", nestedText, 0, true, false},
	{"president_first_name", nestedText, 255, true, false}, {"president_last_name", nestedText, 255, true, false},
	{"federation", nestedText, 100, true, true}, {"enroll_number", nestedText, 100, true, true}, {"sport", nestedText, 100, true, true},
	{"enabled_for", nestedJSON, 0, true, false}, {"checkout_info", nestedText, 0, true, false},
	{"additional_fields", nestedJSON, 0, true, false}, {"stripe_available_methods", nestedJSON, 0, true, false},
	{"iban", nestedText, 34, true, false}, {"website", nestedText, 1024, true, false}, {"abbreviated", nestedText, 100, true, false},
	{"vat_number", nestedText, 20, true, false}, {"whatsapp", nestedText, 20, true, false},
	{"invoice_template", nestedText, 0, true, true}, {"subscription_template", nestedText, 0, true, true},
	{"extra_text_invoices", nestedText, 0, true, false}, {"imported_from_associami", nestedBool, 0, false, false},
	{"show_regulation_to_members", nestedBool, 0, false, false}, {"show_regulation_to_both", nestedBool, 0, false, false},
	{"show_regulation_to_athletes", nestedBool, 0, false, false}, {"show_demand_to_members", nestedBool, 0, false, false},
	{"show_demand_to_both", nestedBool, 0, false, false}, {"show_demand_to_athletes", nestedBool, 0, false, false},
}

// ModuleNestedUniqueID is the UniqueValidator code of the nested primary key.
const ModuleNestedUniqueID = "unique:sport association:sport association id"

// ParseModuleNested ports the validation of the nested SportAssociationSerializer
// that ModulesSerializer(partial=True) runs on request.data['sport_association']:
// the fields present are checked in Meta.fields order and the errors returned in
// that order. taken says whether a SportAssociation (soft-deleted ones included)
// already has the primary key.
func ParseModuleNested(object domain.RequestObject, taken func(id string) (bool, error), form bool) ([]DRFFieldError, error) {
	var errors []DRFFieldError
	for _, field := range moduleNestedFields {
		value, present := object.Lookup(field.name)
		if !present {
			continue
		}
		if form {
			// Field.get_value on the parsed 'sport_association.<key>' input: an empty value
			// of a nullable field is None (or '' when blank is allowed), of an optional
			// one is skipped (or '' when blank is allowed); a required field keeps ''.
			if text, _ := value.(string); text == "" {
				switch {
				case field.null && field.blank:
				case field.null:
					value = nil
				case field.name == "denomination" || field.name == "tax_code":
				default:
					continue
				}
			}
			if field.kind == nestedJSON {
				if text, isText := value.(string); isText {
					// JSONField.get_value marks HTML input as a JSON string: json.loads.
					if _, err := ParseMessageMutationsJSON([]byte(text)); err != nil {
						errors = append(errors, DRFFieldError{Field: field.name, Codes: []string{ModuleJSON}})
					}
					continue
				}
			}
		}
		var codes []string
		switch field.kind {
		case nestedText:
			_, codes = modelTextValue(SupplierField{Name: field.name, Max: field.max, Null: field.null, Blank: field.blank}, value, false)
		case nestedCap:
			codes = moduleNestedCap(value)
		case nestedDecimal:
			if field.null && value != nil && trimmedEmpty(PythonStr(value)) {
				break // DecimalField.validate_empty_values: a blank value of a nullable field is None
			}
			if value == nil {
				if !field.null {
					codes = []string{PaymentCategoryValidationNull}
				}
				break
			}
			if _, code := DRFDecimal(value, 9, 2); code != "" {
				codes = []string{code}
			}
		case nestedBool:
			_, codes = drfBooleanField(value)
		case nestedJSON:
			if value == nil && !field.null {
				codes = []string{PaymentCategoryValidationNull}
			}
		case nestedUUID:
			id, invalid := drfUUIDField(value)
			codes = invalid
			if len(codes) == 0 {
				used, err := taken(id)
				if err != nil {
					return nil, err
				}
				if used {
					codes = []string{ModuleNestedUniqueID}
				}
			}
		}
		if len(codes) > 0 {
			errors = append(errors, DRFFieldError{Field: field.name, Codes: codes})
		}
	}
	return errors, nil
}

func trimmedEmpty(text string) bool {
	for _, r := range text {
		if !unicode.IsSpace(r) {
			return false
		}
	}
	return true
}

// moduleNestedCap is CharField(max_length=5, null=True) with the
// RegexValidator ^\d{5}$ (message 'Enter exactly 5 digits'): the validators run
// in that order and report every failure.
func moduleNestedCap(value any) []string {
	if value == nil {
		return nil
	}
	text, codes := parsePaymentCategoryString(value, int(^uint(0)>>1))
	if len(codes) > 0 {
		return codes
	}
	var failures []string
	digits := 0
	for _, r := range text {
		if _, ok := pythonDigit(r); !ok {
			digits = -1
			break
		}
		digits++
	}
	if digits != 5 {
		failures = append(failures, ModuleCapPattern)
	}
	if utf8.RuneCountInString(text) > 5 {
		failures = append(failures, "max:5")
	}
	return failures
}
