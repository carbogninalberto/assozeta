package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrModuleTemplateServer stands for the views' unhandled exceptions:
// assigning into a non-dict or immutable form request.data, .get on a
// non-dict body, and an invalid or non-iterable bulk ID value (HTTP 500).
var ErrModuleTemplateServer = errors.New("module template request raised")

// ErrModuleTemplatesNoInstances is the bulk view's {"exception": "No instances provided"}.
var ErrModuleTemplatesNoInstances = errors.New("No instances provided")

const ModuleTemplateUniqueID = "unique:sport association module templates:sport association module templates id"

// ModuleTemplateWrite ports SportAssociationModuleTemplatesSerializer
// (fields='__all__') for creation; Errors holds DRF validation codes.
type ModuleTemplateWrite struct {
	ID     *string
	Row    domain.ModuleTemplate
	Errors map[string]any
}

// ParseModuleTemplateWrite applies the view's request.data assignment and the
// serializer's field rules. The association is the view's own value.
func ParseModuleTemplateWrite(body any, form bool, association string) (ModuleTemplateWrite, error) {
	object, ok := body.(domain.RequestObject)
	if form || !ok {
		return ModuleTemplateWrite{}, ErrModuleTemplateServer
	}
	result := ModuleTemplateWrite{Errors: map[string]any{},
		Row: domain.ModuleTemplate{AssociationID: association, Footer: true, Header: true}}
	fail := func(field string, codes ...string) { result.Errors[field] = codes }
	if value, present := object.Lookup("sport_association_module_templates_id"); present {
		if value == nil {
			fail("sport_association_module_templates_id", PaymentCategoryValidationNull)
		} else if id, err := parsePaymentCategoryUUID(value); err != nil {
			fail("sport_association_module_templates_id", PaymentCategoryValidationUUID)
		} else {
			result.ID = &id
		}
	}
	if value, present := object.Lookup("name"); !present {
		fail("name", PaymentCategoryValidationRequired)
	} else if text, codes := parsePaymentCategoryString(value, 150); len(codes) > 0 {
		fail("name", codes...)
	} else {
		result.Row.Name = text
	}
	for _, text := range []struct {
		name   string
		target **string
	}{{"template", &result.Row.Template}, {"custom_footer", &result.Row.CustomFooter}, {"custom_header", &result.Row.CustomHeader}} {
		value, present := object.Lookup(text.name)
		if !present || value == nil {
			continue
		}
		parsed, codes := parsePaymentCategoryString(value, 1<<31-1)
		if len(codes) > 0 {
			fail(text.name, codes...)
			continue
		}
		*text.target = &parsed
	}
	for _, flag := range []struct {
		name   string
		target *bool
	}{{"footer", &result.Row.Footer}, {"header", &result.Row.Header}, {"custom_footer_header", &result.Row.CustomFooterHeader}} {
		value, present := object.Lookup(flag.name)
		if !present {
			continue
		}
		if value == nil {
			fail(flag.name, PaymentCategoryValidationNull)
			continue
		}
		parsed, err := parsePaymentCategoryBoolean(value)
		if err != nil {
			fail(flag.name, PaymentCategoryValidationBoolean)
			continue
		}
		*flag.target = parsed
	}
	return result, nil
}

// ModuleTemplateUpdate is SportAssociationModuleTemplatesSerializer(partial=True)
// validated data: only present fields are set. Association is the related
// sport_association pk (AssociationRaw: the submitted value for messages).
type ModuleTemplateUpdate struct {
	ID             *string
	Text           map[string]*string
	Flags          map[string]bool
	Association    *string
	AssociationRaw string
	Errors         map[string]any
}

var moduleTemplateTextFields = []SupplierField{{Name: "name", Max: 150}, {Name: "template", Null: true},
	{Name: "custom_footer", Null: true}, {Name: "custom_header", Null: true}}

// ParseModuleTemplateUpdate applies the field rules of a partial update. A
// non-dict body is DRF's non_field_errors; form input follows Field.get_value
// (an empty nullable text is None, absent booleans stay unset).
func ParseModuleTemplateUpdate(body any, form bool) ModuleTemplateUpdate {
	result := ModuleTemplateUpdate{Text: map[string]*string{}, Flags: map[string]bool{}, Errors: map[string]any{}}
	object, ok := body.(domain.RequestObject)
	if !ok {
		result.Errors["non_field_errors"] = []string{"dictionary:" + pythonTypeName(body)}
		return result
	}
	if value, present := object.Lookup("sport_association_module_templates_id"); present {
		if value == nil {
			result.Errors["sport_association_module_templates_id"] = []string{PaymentCategoryValidationNull}
		} else if id, err := parsePaymentCategoryUUID(value); err != nil {
			result.Errors["sport_association_module_templates_id"] = []string{PaymentCategoryValidationUUID}
		} else {
			result.ID = &id
		}
	}
	for _, field := range moduleTemplateTextFields {
		value, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		text, codes := modelTextValue(field, value, form)
		if len(codes) > 0 {
			result.Errors[field.Name] = codes
			continue
		}
		result.Text[field.Name] = text
	}
	for _, name := range []string{"footer", "header", "custom_footer_header"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		flag, code := drfBoolean(value, false)
		if code != "" {
			result.Errors[name] = []string{code}
			continue
		}
		result.Flags[name] = *flag
	}
	if value, present := object.Lookup("sport_association"); present {
		// RelatedField.run_validation turns '' into None.
		if value == nil || value == "" {
			result.Errors["sport_association"] = []string{PaymentCategoryValidationNull}
		} else if id, code := supplierRelated(value); code != "" {
			result.Errors["sport_association"] = []string{code}
		} else {
			result.Association, result.AssociationRaw = &id, PythonStr(value)
		}
	}
	return result
}

// Apply returns the row with the validated values assigned.
func (u ModuleTemplateUpdate) Apply(row domain.ModuleTemplate) domain.ModuleTemplate {
	if u.ID != nil {
		row.ID = *u.ID
	}
	if u.Association != nil {
		row.AssociationID = *u.Association
	}
	for name, value := range u.Text {
		switch name {
		case "name":
			row.Name = *value
		case "template":
			row.Template = value
		case "custom_footer":
			row.CustomFooter = value
		case "custom_header":
			row.CustomHeader = value
		}
	}
	for name, value := range u.Flags {
		switch name {
		case "footer":
			row.Footer = value
		case "header":
			row.Header = value
		case "custom_footer_header":
			row.CustomFooterHeader = value
		}
	}
	return row
}

// ModuleTemplateAuditValues is auditlog's str() of every field (nil: None).
func ModuleTemplateAuditValues(row domain.ModuleTemplate) map[string]*string {
	flag := func(value bool) *string {
		text := "False"
		if value {
			text = "True"
		}
		return &text
	}
	id, association, name := row.ID, row.AssociationID, row.Name
	return map[string]*string{"sport_association_module_templates_id": &id, "sport_association": &association, "name": &name,
		"template": row.Template, "footer": flag(row.Footer), "header": flag(row.Header),
		"custom_footer_header": flag(row.CustomFooterHeader), "custom_footer": row.CustomFooter, "custom_header": row.CustomHeader}
}

// ParseModuleTemplateIDs follows request.data.get(key, []), the falsy check
// and Django's UUIDField preparation of every __in value. nil IDs are
// discarded by the lookup. String items are returned raw for the caller's
// Python uuid.UUID(hex=...) port; integers and booleans as canonical UUIDs.
func ParseModuleTemplateIDs(body any) ([]string, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, ErrModuleTemplateServer
	}
	value, _ := object.Lookup("sport_association_module_templates_ids")
	var items []any
	switch v := value.(type) {
	case nil:
		return nil, ErrModuleTemplatesNoInstances
	case bool:
		if !v {
			return nil, ErrModuleTemplatesNoInstances
		}
		return nil, ErrModuleTemplateServer
	case json.Number:
		if number, _ := new(big.Float).SetString(string(v)); number == nil || number.Sign() == 0 {
			return nil, ErrModuleTemplatesNoInstances
		}
		return nil, ErrModuleTemplateServer
	case string:
		if v == "" {
			return nil, ErrModuleTemplatesNoInstances
		}
		for _, r := range v {
			items = append(items, string(r))
		}
	case []any:
		items = v
	case domain.RequestObject:
		// Iterating a dict yields its keys.
		for _, field := range v {
			items = append(items, field.Name)
		}
	}
	if len(items) == 0 {
		return nil, ErrModuleTemplatesNoInstances
	}
	ids := make([]string, 0, len(items))
	for _, item := range items {
		switch v := item.(type) {
		case nil:
			continue
		case string:
			ids = append(ids, v)
		case bool, json.Number:
			if number, ok := item.(json.Number); ok && strings.ContainsAny(string(number), ".eE") {
				return nil, ErrModuleTemplateServer
			}
			id, err := parsePaymentCategoryUUID(v)
			if err != nil {
				return nil, ErrModuleTemplateServer
			}
			ids = append(ids, id)
		default:
			return nil, ErrModuleTemplateServer
		}
	}
	return ids, nil
}

// ModuleTemplateRow is serializer.data in declared field order.
type ModuleTemplateRow struct {
	ID                 string  `json:"sport_association_module_templates_id"`
	Name               string  `json:"name"`
	Template           *string `json:"template"`
	Footer             bool    `json:"footer"`
	Header             bool    `json:"header"`
	CustomFooterHeader bool    `json:"custom_footer_header"`
	CustomFooter       *string `json:"custom_footer"`
	CustomHeader       *string `json:"custom_header"`
	AssociationID      string  `json:"sport_association"`
}

func ModuleTemplateRowFrom(row domain.ModuleTemplate) ModuleTemplateRow {
	return ModuleTemplateRow{ID: row.ID, Name: row.Name, Template: row.Template, Footer: row.Footer, Header: row.Header,
		CustomFooterHeader: row.CustomFooterHeader, CustomFooter: row.CustomFooter, CustomHeader: row.CustomHeader, AssociationID: row.AssociationID}
}
