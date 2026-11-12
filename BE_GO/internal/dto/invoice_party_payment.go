package dto

import (
	"math"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// NestedPaymentLookups are the database checks of the nested serializers.
type NestedPaymentLookups struct {
	// Related is a PrimaryKeyRelatedField lookup: false when id (canonical UUID text) is not in the field's queryset.
	// A non-nil error aborts the whole validation and is returned as is (the caller uses it for a row owned by
	// another association, which DRF answers with 403 from inside validation).
	Related func(model, id string) (bool, error)
	// Taken is a primary key's UniqueValidator: true when the id is already used.
	Taken func(model, id string) (bool, error)
}

// ValidateNestedPayment validates the nested payment value of the invoice-parties add/update views with
// PaymentSerializer(data=value, partial=partial): errs is nil when the value is valid, else serializer.errors of the
// `payment` key (field -> []string codes, or a nested serializer's map). notPorted marks an input an existing helper
// reports as unported. err is the Related lookup's error (a foreign row: DRF raises PermissionDenied from inside
// validation, so every other error is dropped).
func ValidateNestedPayment(value any, partial, form bool, lookups NestedPaymentLookups) (errs any, notPorted bool, err error) {
	validator := nestedPaymentValidator{partial: partial, form: form, lookups: lookups}
	return validator.object(value, nestedPaymentSerializer)
}

// nestedPaymentKind is the field class of the nested serializers (the repr's field types).
type nestedPaymentKind uint8

const (
	nestedPaymentText nestedPaymentKind = iota
	nestedPaymentCap
	nestedPaymentUUID
	nestedPaymentUniqueUUID
	nestedPaymentChoice
	nestedPaymentBoolean
	nestedPaymentDecimal
	nestedPaymentInteger
	nestedPaymentDateTime
	nestedPaymentDate
	nestedPaymentJSON
	nestedPaymentRelated
	nestedPaymentMany
	nestedPaymentNested
)

// nestedPaymentField is one field of a nested serializer: the repr's options plus the error code of a
// UniqueValidator and the Related model key.
type nestedPaymentField struct {
	name                  string
	kind                  nestedPaymentKind
	required, null, blank bool
	max                   int
	email                 bool
	choices               []string
	maxDigits, places     int
	minValue, maxValue    int64
	model                 string // Related key
	takenModel            string // Taken key
	unique                string // "unique:" suffix: <model verbose>:<field verbose>
	child                 *nestedPaymentSchema
}

type nestedPaymentSchema struct{ fields []nestedPaymentField }

// nestedPaymentSerializer is PaymentSerializer's explicit writable field order (the repr). The duplicate
// creation_date and the read-only SerializerMethodFields are omitted.
var nestedPaymentSerializer = &nestedPaymentSchema{fields: []nestedPaymentField{
	{name: "payment_id", kind: nestedPaymentUniqueUUID, takenModel: "payment", unique: "payment:payment id"},
	{name: "user", kind: nestedPaymentNested, required: true, child: nestedPaymentUserSerializer},
	{name: "associate", kind: nestedPaymentNested, required: true, child: nestedPaymentAssociateSerializer},
	{name: "instructor", kind: nestedPaymentNested, required: true, child: nestedPaymentInstructorSerializer},
	{name: "supplier", kind: nestedPaymentNested, required: true, child: nestedPaymentSupplierSerializer},
	{name: "invoice", kind: nestedPaymentNested, null: true, child: nestedPaymentInvoiceSerializer},
	{name: "type", kind: nestedPaymentChoice, choices: PaymentTypes},
	{name: "subject", kind: nestedPaymentChoice, choices: []string{"0", "1", "2", "3"}},
	{name: "amount", kind: nestedPaymentDecimal, maxDigits: 9, places: 2},
	{name: "creation_date", kind: nestedPaymentDateTime},
	{name: "payment_date", kind: nestedPaymentDateTime, null: true},
	{name: "paid", kind: nestedPaymentBoolean},
	{name: "expense", kind: nestedPaymentBoolean},
	{name: "description", kind: nestedPaymentText, null: true, max: 5120},
	{name: "meta_payment_categories", kind: nestedPaymentJSON, null: true},
	{name: "payment_intent_id", kind: nestedPaymentText, null: true, max: 125},
	{name: "archived", kind: nestedPaymentBoolean},
	{name: "invoice_fiscal_code", kind: nestedPaymentText, null: true, max: 125},
	{name: "meta", kind: nestedPaymentJSON, null: true},
	{name: "notes", kind: nestedPaymentText, null: true, blank: true, max: 5120},
	{name: "deleted", kind: nestedPaymentBoolean},
	{name: "course", kind: nestedPaymentJSON, null: true},
	{name: "payment_category", kind: nestedPaymentRelated, null: true, model: "payment_category"},
	{name: "sport_association", kind: nestedPaymentRelated, null: true, model: "sport_association"},
	{name: "custom_accounts", kind: nestedPaymentRelated, null: true, model: "custom_accounts"},
	{name: "signature", kind: nestedPaymentRelated, null: true, model: "signature"},
	{name: "group", kind: nestedPaymentRelated, null: true, model: "group"},
}}

var nestedPaymentUserSerializer = &nestedPaymentSchema{fields: []nestedPaymentField{
	{name: "first_name", kind: nestedPaymentText, blank: true, max: 150},
	{name: "last_name", kind: nestedPaymentText, blank: true, max: 150},
	{name: "email", kind: nestedPaymentText, blank: true, max: 254, email: true},
	{name: "phone", kind: nestedPaymentText, null: true, blank: true, max: 20},
	{name: "username", kind: nestedPaymentText},
	{name: "role", kind: nestedPaymentChoice, choices: []string{"1", "2", "3"}},
}}

var nestedPaymentAssociateSerializer = &nestedPaymentSchema{fields: []nestedPaymentField{
	{name: "associate_id", kind: nestedPaymentText},
	{name: "address", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "address_cap", kind: nestedPaymentCap, null: true, blank: true, max: 5},
	{name: "address_city", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "born_city", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "born_date", kind: nestedPaymentDate, null: true},
	{name: "email", kind: nestedPaymentText, null: true, blank: true, max: 254, email: true},
	{name: "sex", kind: nestedPaymentChoice, blank: true, choices: []string{"A", "M", "F"}},
	{name: "first_name", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "last_name", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "is_minor", kind: nestedPaymentBoolean},
	{name: "sport_association", kind: nestedPaymentRelated, null: true, model: "sport_association"},
	{name: "tax_code", kind: nestedPaymentText, null: true, blank: true, max: 25},
	{name: "phone", kind: nestedPaymentText, null: true, blank: true, max: 30},
	{name: "family", kind: nestedPaymentRelated, null: true, model: "family"},
	{name: "family_role", kind: nestedPaymentText, null: true, blank: true, max: 100},
}}

var nestedPaymentInstructorSerializer = &nestedPaymentSchema{fields: []nestedPaymentField{
	{name: "instructor_id", kind: nestedPaymentUniqueUUID, takenModel: "instructor", unique: "instructor:instructor id"},
	{name: "first_name", kind: nestedPaymentText, required: true, max: 150},
	{name: "last_name", kind: nestedPaymentText, required: true, max: 150},
	{name: "email", kind: nestedPaymentText, null: true, blank: true, max: 254, email: true},
	{name: "born_date", kind: nestedPaymentDate, null: true},
	{name: "born_city", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "born_province", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "tax_code", kind: nestedPaymentText, null: true, blank: true, max: 25},
	{name: "address_city", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "address", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "address_province", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "civic_number", kind: nestedPaymentText, null: true, blank: true, max: 10},
	{name: "study_title", kind: nestedPaymentText, null: true, blank: true, max: 150},
	{name: "role", kind: nestedPaymentText, null: true, max: 150},
	{name: "phone", kind: nestedPaymentText, null: true, blank: true, max: 20},
	{name: "phone_2", kind: nestedPaymentText, null: true, blank: true, max: 20},
	{name: "phone_3", kind: nestedPaymentText, null: true, blank: true, max: 20},
	{name: "phone_4", kind: nestedPaymentText, null: true, blank: true, max: 20},
	{name: "draft", kind: nestedPaymentBoolean},
	{name: "associated_user_id", kind: nestedPaymentUUID, null: true},
	{name: "is_volunteer", kind: nestedPaymentBoolean},
	{name: "stipulated_contract_in", kind: nestedPaymentDate, null: true},
	{name: "default_hourly_billing", kind: nestedPaymentDecimal, maxDigits: 9, places: 2},
	{name: "default_percentage_billing", kind: nestedPaymentDecimal, null: true, maxDigits: 4, places: 2},
	{name: "user", kind: nestedPaymentRelated, required: true, model: "user"},
	{name: "documents", kind: nestedPaymentMany, model: "document"},
}}

var nestedPaymentInvoiceSerializer = &nestedPaymentSchema{fields: []nestedPaymentField{
	{name: "invoice_id", kind: nestedPaymentUniqueUUID, takenModel: "invoice", unique: "invoice:invoice id"},
	{name: "creation_date", kind: nestedPaymentDateTime},
	{name: "membership_fee", kind: nestedPaymentDecimal, required: true, maxDigits: 9, places: 2},
	{name: "activity_fee", kind: nestedPaymentDecimal, required: true, maxDigits: 9, places: 2},
	{name: "meta_payment_categories", kind: nestedPaymentJSON, null: true},
	{name: "description", kind: nestedPaymentText, null: true, max: 255},
	{name: "number", kind: nestedPaymentInteger, null: true, minValue: math.MinInt32, maxValue: math.MaxInt32},
	{name: "archived", kind: nestedPaymentBoolean},
	{name: "cancelled", kind: nestedPaymentBoolean},
	{name: "meta", kind: nestedPaymentJSON, null: true},
	{name: "imported_from_associami", kind: nestedPaymentBoolean, null: true},
	{name: "deleted", kind: nestedPaymentBoolean},
	{name: "document_pdf", kind: nestedPaymentRelated, null: true, model: "document"},
	{name: "sport_association", kind: nestedPaymentRelated, required: true, model: "sport_association"},
	{name: "selected_tutor", kind: nestedPaymentRelated, null: true, model: "associate"},
	{name: "group", kind: nestedPaymentRelated, null: true, model: "group"},
	{name: "attached_documents", kind: nestedPaymentMany, model: "document"},
}}

// nestedPaymentSupplierSerializer is SupplierSerializer with the text options of SupplierTextFields (the same
// model fields the nested serializer carries), in the nested repr's order.
var nestedPaymentSupplierSerializer = nestedPaymentSupplier()

func nestedPaymentSupplier() *nestedPaymentSchema {
	fields := []nestedPaymentField{{name: "supplier_id", kind: nestedPaymentUniqueUUID,
		takenModel: "supplier", unique: "supplier and customers:supplier id"}}
	for _, text := range SupplierTextFields {
		fields = append(fields, nestedPaymentField{name: text.Name, kind: nestedPaymentText, required: text.Required,
			null: text.Null, blank: text.Blank, max: text.Max, email: text.Email})
	}
	return &nestedPaymentSchema{fields: append(fields,
		nestedPaymentField{name: "deleted", kind: nestedPaymentBoolean},
		nestedPaymentField{name: "sport_association", kind: nestedPaymentRelated, null: true, model: "sport_association"},
		nestedPaymentField{name: "group", kind: nestedPaymentRelated, null: true, model: "group"},
	)}
}

// nestedPaymentValidator holds the view's partial/form flags and the lookups.
type nestedPaymentValidator struct {
	partial bool
	form    bool
	lookups NestedPaymentLookups
}

// object validates one serializer level: serializer.errors, or nil when valid.
func (v *nestedPaymentValidator) object(value any, serializer *nestedPaymentSchema) (any, bool, error) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(value)}}, false, nil
	}
	errs := map[string]any{}
	for _, field := range serializer.fields {
		fieldValue, present, codes, notPorted := v.input(object, field)
		if notPorted {
			return nil, true, nil
		}
		if len(codes) > 0 {
			errs[field.name] = codes
			continue
		}
		if !present {
			if field.required && !v.partial {
				errs[field.name] = []string{PaymentCategoryValidationRequired}
			}
			continue
		}
		problem, notPorted, err := v.validate(field, fieldValue)
		if err != nil {
			return nil, false, err
		}
		if notPorted {
			return nil, true, nil
		}
		if problem != nil {
			errs[field.name] = problem
		}
	}
	if len(errs) > 0 {
		return errs, false, nil
	}
	return nil, false, nil
}

// input is Field.get_value of one field: the raw JSON value, or DRF's HTML-form extraction (a nested serializer's
// "<name>." fields, a many field's getlist, a JSONField's json.loads, and the empty-input rules).
func (v *nestedPaymentValidator) input(object domain.RequestObject, field nestedPaymentField) (value any, present bool, codes []string, notPorted bool) {
	if !v.form {
		value, present = object.Lookup(field.name)
		return value, present, nil, false
	}
	switch field.kind {
	case nestedPaymentNested:
		child, found := nestedPaymentFormChild(object, field.name)
		return child, found, nil, false
	case nestedPaymentMany:
		values, found := object.List(field.name)
		if !found {
			if v.partial {
				return nil, false, nil, false
			}
			return []any{}, true, nil, false
		}
		return values, true, nil, false
	case nestedPaymentJSON:
		value, found := object.Lookup(field.name)
		if !found {
			return nil, false, nil, false
		}
		if upload, isUpload := value.(domain.RequestUpload); isUpload {
			value = upload.Name
		}
		tree, valid, _, deep := automationFormTree(value)
		switch {
		case deep:
			return nil, false, nil, true
		case !valid:
			return nil, false, []string{CourseSubscriptionJSONInvalid}, false
		}
		return tree, true, nil, false
	}
	value, present = object.Lookup(field.name)
	if !present {
		if field.kind == nestedPaymentBoolean && !v.partial {
			return false, true, nil, false // BooleanField.default_empty_html
		}
		return nil, false, nil, false
	}
	if text, isText := value.(string); isText && text == "" {
		switch {
		case field.null && field.blank:
			return "", true, nil, false
		case field.null:
			return nil, true, nil, false
		case !field.required:
			return nil, false, nil, false // get_value: an empty input of an optional field is skipped
		}
	}
	return value, true, nil, false
}

// nestedPaymentFormChild is html.parse_html_dict of one nested serializer: the "<name>." fields with the prefix
// removed, preserving repeated values (RequestField.All).
func nestedPaymentFormChild(object domain.RequestObject, name string) (domain.RequestObject, bool) {
	prefix := name + "."
	child := domain.RequestObject{}
	for _, field := range object {
		if strings.HasPrefix(field.Name, prefix) {
			child = append(child, domain.RequestField{Name: strings.TrimPrefix(field.Name, prefix),
				Value: field.Value, All: field.All})
		}
	}
	return child, len(child) > 0
}

// validate is Field.run_validation + to_internal_value of one present field.
func (v *nestedPaymentValidator) validate(field nestedPaymentField, value any) (any, bool, error) {
	if value == nil {
		if field.null {
			return nil, false, nil
		}
		return []string{PaymentCategoryValidationNull}, false, nil
	}
	switch field.kind {
	case nestedPaymentText:
		_, codes := modelTextValue(SupplierField{Name: field.name, Max: field.max, Null: field.null,
			Blank: field.blank, Email: field.email}, value, v.form)
		return nestedPaymentCodes(codes), false, nil
	case nestedPaymentCap:
		_, codes := associateText(SupplierField{Name: field.name, Max: field.max, Null: field.null,
			Blank: field.blank}, value)
		return nestedPaymentCodes(codes), false, nil
	case nestedPaymentUUID:
		_, codes := drfUUIDField(value)
		return nestedPaymentCodes(codes), false, nil
	case nestedPaymentUniqueUUID:
		id, codes := drfUUIDField(value)
		if len(codes) > 0 {
			return codes, false, nil
		}
		if v.lookups.Taken == nil {
			return nil, false, nil
		}
		taken, err := v.lookups.Taken(field.takenModel, id)
		if err != nil {
			return nil, false, err
		}
		if taken {
			return []string{"unique:" + field.unique}, false, nil
		}
		return nil, false, nil
	case nestedPaymentChoice:
		if text, isText := value.(string); isText && text == "" && field.blank {
			return nil, false, nil
		}
		_, code := paymentEntryChoice(value, field.choices)
		return nestedPaymentCode(code), false, nil
	case nestedPaymentBoolean:
		_, code := drfBoolean(value, field.null)
		return nestedPaymentCode(code), false, nil
	case nestedPaymentDecimal:
		_, code := drfDecimal(value, field.maxDigits, field.places)
		return nestedPaymentCode(code), false, nil
	case nestedPaymentInteger:
		_, codes := drfInteger(value, field.null, field.minValue, field.maxValue)
		return nestedPaymentCodes(codes), false, nil
	case nestedPaymentDateTime:
		_, code := drfDateTime(value, field.null)
		return nestedPaymentCode(code), false, nil
	case nestedPaymentDate:
		_, ok, err := ParseDMYDate(value)
		if err != nil {
			return nil, true, nil
		}
		if !ok {
			return []string{PersonaDateFormat}, false, nil
		}
		return nil, false, nil
	case nestedPaymentJSON:
		// Any parsed JSON value is a valid JSONField input (form text was parsed by input()).
		return nil, false, nil
	case nestedPaymentRelated:
		related, code := drfRelated(value, field.null)
		if code != "" {
			return []string{code}, false, nil
		}
		if related.ID == nil {
			return nil, false, nil
		}
		exists, err := v.related(field.model, *related.ID)
		if err != nil {
			return nil, false, err
		}
		if !exists {
			return []string{"related-does-not-exist:" + *related.ID}, false, nil
		}
		return nil, false, nil
	case nestedPaymentMany:
		return v.many(field.model, value)
	case nestedPaymentNested:
		_, isObject, problem := nestedObject(value, field.null)
		switch {
		case problem != nil:
			return problem, false, nil
		case isObject:
			return v.object(value, field.child)
		}
		return nil, false, nil
	}
	return nil, false, nil
}

// many is PrimaryKeyRelatedField(many=True).to_internal_value: a list whose first failing item is the only error.
func (v *nestedPaymentValidator) many(model string, value any) (any, bool, error) {
	items, ok := value.([]any)
	if !ok {
		return []string{"not-a-list:" + pythonTypeName(value)}, false, nil
	}
	for _, item := range items {
		if item == nil {
			return []string{"related-does-not-exist:" + PythonStr(item)}, false, nil
		}
		id, code := supplierRelated(item)
		if code != "" {
			return []string{code}, false, nil
		}
		exists, err := v.related(model, id)
		if err != nil {
			return nil, false, err
		}
		if !exists {
			return []string{"related-does-not-exist:" + id}, false, nil
		}
	}
	return nil, false, nil
}

func (v *nestedPaymentValidator) related(model, id string) (bool, error) {
	if v.lookups.Related == nil {
		return true, nil
	}
	return v.lookups.Related(model, id)
}

// nestedPaymentCodes is a field's error list, nil when the value is valid (a nil []string must not become a
// non-nil any).
func nestedPaymentCodes(codes []string) any {
	if len(codes) == 0 {
		return nil
	}
	return codes
}

// nestedPaymentCode is one error code of a helper that reports validity as an empty string.
func nestedPaymentCode(code string) any {
	if code == "" {
		return nil
	}
	return []string{code}
}
