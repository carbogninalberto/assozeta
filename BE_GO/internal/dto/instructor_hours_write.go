package dto

import (
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// InstructorHoursJSON is JSONField's 'invalid' for HTML input json.loads rejects.
const InstructorHoursJSON = "instructor-hours-json"

// InstructorHoursFormJSONDepth is the measured form JSON text band of the two
// JSON fields (owner decision X15), measured on the parity harness's Django
// (CPython 3.13.16, default recursion limit) with an HTML form PATCH whose
// courses (or calculation_data) is {"deep": <n nested objects>} or
// [<n nested objects>]: D is auditJSONDepth of the text (n + 1 for those
// shapes). D <= 9941 validates; from D 9942 DRF's JSONField json.loads raises
// RecursionError, the plain 500 page before any write, even when another field
// is invalid (with hours=abc the 400 is answered up to D 9941 and the 500 page
// from 9942). Below the band the parsed value's own depth (JSONValueDepth)
// decides the save-time json.dumps band, which the service answers.
const InstructorHoursFormJSONDepth = 9942

// errInstructorHoursFormJSONTooDeep is DRF's RecursionError on a form JSON
// field nested past InstructorHoursFormJSONDepth: the plain 500 page, before
// any write. An application state (the 500 page), never a handoff.
var errInstructorHoursFormJSONTooDeep = refusal.AppState("instructor hours: form JSON nested past the measured depth")

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
	// JSONTextDepth and JSONValueDepth hold, for an HTML-form JSON field, the
	// maximum nesting the parser read from its text (auditJSONDepth) and the
	// container nesting of the parsed value (JSONValueDepth). The save-time
	// dump bands read the value depth; a JSON body records neither.
	JSONTextDepth  map[string]int
	JSONValueDepth map[string]int
}

// instructorHoursFormJSON is DRF's JSONField.get_value/to_internal_value for an
// HTML form value: json.loads of the text with Python's NaN, Infinity and
// -Infinity constants, read up to formJSONNesting (past every measured band).
// ok=false is json.loads' JSONDecodeError ("Value must be valid JSON.");
// nonFinite marks a value the jsonb write cannot store (a NaN or Infinity
// literal, a lone surrogate escape, or a number beyond the double range);
// tooDeep marks text nested past InstructorHoursFormJSONDepth. textDepth is
// auditJSONDepth of the text the scanner reaches (a syntax error stops it) and
// valueDepth is JSONValueDepth of the parsed value, an overwritten duplicate
// key's deep text no longer counting.
func instructorHoursFormJSON(text string) (tree any, ok, nonFinite, tooDeep bool, textDepth, valueDepth int) {
	textDepth = auditJSONDepth(text)
	data := []byte(text)
	constants := pythonJSONConstants(data)
	var err error
	tree, err = parseProfileTablesJSONConstantsLimit(data, constants, true, formJSONNesting)
	if err != nil {
		// json.loads stops at the first syntax error: only the nesting reached
		// before it can raise RecursionError.
		var syntax *json.SyntaxError
		if errors.As(err, &syntax) && syntax.Offset >= 0 && syntax.Offset <= int64(len(text)) {
			textDepth = auditJSONDepth(text[:syntax.Offset])
		}
	}
	if textDepth >= InstructorHoursFormJSONDepth {
		return nil, false, false, true, textDepth, 0
	}
	switch {
	case errors.Is(err, ErrLoginRateJSONValue):
		// Python's integer digit limit is a ValueError inside json.loads, which
		// DRF's JSONField.to_internal_value catches: the field's 'invalid' 400.
		return nil, false, false, false, textDepth, 0
	case err != nil:
		return nil, false, false, false, textDepth, 0
	}
	if len(constants) > 0 || !AuditFiniteJSON(tree) {
		// A NaN or Infinity literal, or a number beyond the double range: jsonb
		// never stores them, so the field's save fails in Django too.
		return nil, false, true, false, textDepth, 0
	}
	if auditLoneSurrogate(text) {
		// json.loads keeps the lone code unit; the jsonb write refuses it.
		return nil, false, true, false, textDepth, 0
	}
	return tree, true, false, false, textDepth, JSONValueDepth(tree)
}

// ParseInstructorHoursWrite ports InstructorHoursSerializer field validation
// (fields '__all__'; document_token and the primary key instructor_hours_id are
// read-only). The related querysets and the tenant scope are checked by the
// service.
func ParseInstructorHoursWrite(body any, form, partial bool) (InstructorHoursWrite, map[string]any, error) {
	write := InstructorHoursWrite{Text: map[string]*string{}, Decimals: map[string]*string{}, JSON: map[string]any{},
		JSONTextDepth: map[string]int{}, JSONValueDepth: map[string]int{}}
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
			parsed, ok, nonFinite, tooDeep, textDepth, valueDepth := instructorHoursFormJSON(text)
			if tooDeep {
				return write, nil, errInstructorHoursFormJSONTooDeep
			}
			write.JSONTextDepth[name] = textDepth
			if nonFinite {
				write.NonFinite = true
				continue
			}
			if !ok {
				errs[name] = []string{InstructorHoursJSON}
				continue
			}
			write.JSONValueDepth[name] = valueDepth
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
