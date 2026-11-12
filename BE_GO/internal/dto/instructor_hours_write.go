package dto

import (
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InstructorHoursJSON is JSONField's 'invalid' for HTML input json.loads rejects.
const InstructorHoursJSON = "instructor-hours-json"

// InstructorHoursTextFields are the model's text fields.
var InstructorHoursTextFields = []SupplierField{
	{Name: "compensation_type", Max: 15},
	{Name: "period", Max: 50, Null: true, Blank: true},
	{Name: "notes", Null: true, Blank: true},
}

// InstructorHoursDecimal is one DecimalField of the model.
type InstructorHoursDecimal struct {
	Name              string
	MaxDigits, Places int
	Null, Required    bool
}

var InstructorHoursDecimals = []InstructorHoursDecimal{
	{Name: "hours", MaxDigits: 4, Places: 2, Required: true},
	{Name: "hourly_billing", MaxDigits: 9, Places: 2},
	{Name: "percentage_billing", MaxDigits: 4, Places: 2, Null: true},
	{Name: "amount", MaxDigits: 9, Places: 2},
}

// InstructorHoursWrite is InstructorHoursSerializer's validated data before
// the related lookups. Maps hold present fields (nil for None).
type InstructorHoursWrite struct {
	Instructor   SubscriptionMembershipRelation
	Payment      SubscriptionMembershipRelation
	Document     SubscriptionMembershipRelation
	CreationDate *time.Time
	Date         *string
	Paid         *bool
	Text         map[string]*string
	Decimals     map[string]*string
	JSON         map[string]any
	// NonFinite marks HTML JSON input with NaN or Infinity, which the
	// JSONField write cannot store.
	NonFinite bool
}

// ParseInstructorHoursWrite ports InstructorHoursSerializer field validation
// (fields '__all__'; document_token and the primary key instructor_hours_id are
// read-only). The related querysets and the tenant scope are checked by the
// service.
func ParseInstructorHoursWrite(body any, form, partial bool) (InstructorHoursWrite, map[string]any, error) {
	write := InstructorHoursWrite{Text: map[string]*string{}, Decimals: map[string]*string{}, JSON: map[string]any{}}
	if body == nil {
		// Serializer.errors' edge case for a None body.
		return write, map[string]any{"non_field_errors": []string{"no-data"}}, nil
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return write, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}, nil
	}
	errs := map[string]any{}
	// An empty HTML value of a non-null, non-required field is skipped.
	skipped := func(value any, null bool) bool { return form && value == "" && !null }
	if value, present := object.Lookup("creation_date"); present && !skipped(value, false) {
		if value == nil {
			errs["creation_date"] = []string{PaymentCategoryValidationNull}
		} else if text, ok := value.(string); !ok {
			errs["creation_date"] = []string{PaymentCategoryValidationDateTime}
		} else if parsed, err := parsePaymentCategoryDate(text); err != nil {
			if errors.Is(err, errPaymentCategoryDateTimeOverflow) {
				errs["creation_date"] = []string{PaymentCategoryValidationDateTimeOverflow}
			} else {
				errs["creation_date"] = []string{PaymentCategoryValidationDateTime}
			}
		} else {
			write.CreationDate = &parsed
		}
	}
	if value, present := object.Lookup("date"); present && !skipped(value, false) {
		if value == nil {
			errs["date"] = []string{PaymentCategoryValidationNull}
		} else if day, ok := ParseSubscriptionMembershipDate(value); ok {
			write.Date = &day
		} else {
			errs["date"] = []string{SubscriptionMembershipDate}
		}
	}
	for _, field := range InstructorHoursTextFields {
		value, present := object.Lookup(field.Name)
		if !present || skipped(value, field.Null) {
			continue
		}
		if form && value == "" && field.Blank {
			empty := ""
			write.Text[field.Name] = &empty
			continue
		}
		text, codes := modelTextValue(field, value, form)
		if len(codes) > 0 {
			errs[field.Name] = codes
			continue
		}
		write.Text[field.Name] = text
	}
	for _, field := range InstructorHoursDecimals {
		value, present := object.Lookup(field.Name)
		if !present {
			if field.Required && !partial {
				errs[field.Name] = []string{PaymentCategoryValidationRequired}
			}
			continue
		}
		if form && value == "" && !field.Null && !field.Required {
			continue
		}
		switch {
		case field.Null && (value == nil || PythonStrip(PythonStr(value)) == ""):
			// DecimalField.validate_empty_values: a blank value of an allow_null decimal is None.
			write.Decimals[field.Name] = nil
		case value == nil:
			errs[field.Name] = []string{PaymentCategoryValidationNull}
		default:
			decimal, code := DRFDecimal(value, field.MaxDigits, field.Places)
			if code != "" {
				errs[field.Name] = []string{code}
				continue
			}
			write.Decimals[field.Name] = &decimal
		}
	}
	for _, name := range []string{"courses", "calculation_data"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if form {
			text, _ := value.(string)
			parsed, ok, err := AuditJSONLoads(text)
			if err != nil {
				write.NonFinite = true
				continue
			}
			if !ok {
				errs[name] = []string{InstructorHoursJSON}
				continue
			}
			if !AuditFiniteJSON(parsed) {
				write.NonFinite = true
			}
			value = parsed
		}
		write.JSON[name] = value
	}
	if value, present := object.Lookup("paid"); present {
		if value == nil {
			errs["paid"] = []string{PaymentCategoryValidationNull}
		} else if paid, err := parsePaymentCategoryBoolean(value); err != nil {
			errs["paid"] = []string{PaymentCategoryValidationBoolean}
		} else {
			write.Paid = &paid
		}
	} else if form && !partial {
		// BooleanField.default_empty_html.
		paid := false
		write.Paid = &paid
	}
	relations := []struct {
		name   string
		null   bool
		target *SubscriptionMembershipRelation
	}{{"instructor", false, &write.Instructor}, {"payment", true, &write.Payment}, {"document", true, &write.Document}}
	for _, relation := range relations {
		value, present := object.Lookup(relation.name)
		if !present {
			if relation.name == "instructor" && !partial {
				errs[relation.name] = []string{PaymentCategoryValidationRequired}
			}
			continue
		}
		parsed, codes := subscriptionMembershipRelated(value, relation.null)
		if codes != nil {
			errs[relation.name] = codes
			continue
		}
		*relation.target = parsed
	}
	if len(errs) > 0 {
		return write, errs, nil
	}
	return write, nil, nil
}
