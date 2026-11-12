package dto

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PersonaDateFormat is DateField's error under DATE_INPUT_FORMATS ['%d/%m/%Y'].
const PersonaDateFormat = "date:DD/MM/YYYY"

// PersonaFields are the parsed values of AssociateSerializer /
// BasicAssociateSerializer: present keys only.
type PersonaFields struct {
	Text      map[string]*string
	Booleans  map[string]bool
	Sex       *string
	BornDate  **time.Time
	Created   *time.Time
	Relations map[string]*string // user, sport_association, group, family; nil value is None
	// AssociateID is BasicAssociateSerializer's writable primary key.
	AssociateID *string
}

// PersonaInvalid is serializer.errors (codes rendered by the handler).
type PersonaInvalid struct{ Fields map[string][]string }

func (e *PersonaInvalid) Error() string { return "invalid associate data" }

// PersonaFieldOptions selects the serializer.
type PersonaFieldOptions struct {
	Partial, Form bool
	// Basic is BasicAssociateSerializer: associate_id and creation_date are writable.
	Basic bool
	// TutorsData validates AssociateSerializer's ListField(required=False).
	TutorsData bool
	// Omit are keys removed before validation (tutor_info.pop(...)).
	Omit []string
}

// PersonaTextFields are the model's CharField/EmailField/TextField columns
// (all null=True, blank=True).
var PersonaTextFields = []SupplierField{
	{Name: "picture_path", Max: 512, Null: true, Blank: true},
	{Name: "first_name", Max: 150, Null: true, Blank: true},
	{Name: "last_name", Max: 150, Null: true, Blank: true},
	{Name: "tax_code", Max: 25, Null: true, Blank: true},
	{Name: "born_city", Max: 150, Null: true, Blank: true},
	{Name: "address_city", Max: 150, Null: true, Blank: true},
	{Name: "address", Max: 150, Null: true, Blank: true},
	{Name: "address_cap", Max: 5, Null: true, Blank: true},
	{Name: "nationality", Max: 150, Null: true, Blank: true},
	{Name: "nationality_residence", Max: 150, Null: true, Blank: true},
	{Name: "email", Max: 254, Null: true, Blank: true, Email: true},
	{Name: "phone", Max: 30, Null: true, Blank: true},
	{Name: "phone_2", Max: 30, Null: true, Blank: true},
	{Name: "phone_3", Max: 30, Null: true, Blank: true},
	{Name: "phone_4", Max: 30, Null: true, Blank: true},
	{Name: "phone_label", Max: 100, Null: true, Blank: true},
	{Name: "phone_2_label", Max: 100, Null: true, Blank: true},
	{Name: "phone_3_label", Max: 100, Null: true, Blank: true},
	{Name: "phone_4_label", Max: 100, Null: true, Blank: true},
	{Name: "family_role", Max: 100, Null: true, Blank: true},
	{Name: "id_number", Max: 100, Null: true, Blank: true},
	{Name: "notes", Null: true, Blank: true},
}

// PersonaBooleanFields are the model's BooleanFields (not null, with defaults).
var PersonaBooleanFields = []string{"is_minor", "disabled", "draft", "deleted", "migrated"}

// PersonaRelationFields are the forward relations (null=True), in field order.
var PersonaRelationFields = []string{"user", "sport_association", "group", "family"}

// PersonaUUIDValue is uuid.UUID(...) as UUIDField.to_python applies it:
// strings (hex forms), ints (int=), booleans (int); false for anything else.
func PersonaUUIDValue(value any) (string, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", true
		}
		return "00000000-0000-0000-0000-000000000000", true
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false
		}
	}
	return supplierUUID(value)
}

// PersonaTruthy is Python truthiness for a decoded request value.
func PersonaTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		number, err := v.Float64()
		return err != nil || number != 0
	case []any:
		return len(v) > 0
	case domain.RequestObject:
		return len(v) > 0
	}
	return true
}

// PersonaDMYUnported reports a born_date text with non-ASCII digits (strptime's
// Unicode digits are not ported).
func PersonaDMYUnported(value any) bool {
	text, ok := value.(string)
	return ok && StrptimeUnicodeDigits(text)
}

// ParsePersonaFields ports the serializer's field validation. related(name,
// id) checks existence (false: does not exist) and returns
// domain.ErrPersonaWriteForeignReference to escape validation (tenant_scope).
func ParsePersonaFields(object domain.RequestObject, options PersonaFieldOptions,
	related func(name, id string) (bool, error)) (PersonaFields, *PersonaInvalid, error) {
	fields := PersonaFields{Text: map[string]*string{}, Booleans: map[string]bool{}, Relations: map[string]*string{}}
	invalid := &PersonaInvalid{Fields: map[string][]string{}}
	omitted := map[string]bool{}
	for _, name := range options.Omit {
		omitted[name] = true
	}
	lookup := func(name string) (any, bool) {
		if omitted[name] {
			return nil, false
		}
		value, present := object.Lookup(name)
		if present && options.Form && value == "" {
			// Field.get_value: an empty HTML input of a nullable field is '' when
			// blank is allowed, otherwise None.
			switch name {
			case "born_date", "user", "sport_association", "group", "family":
				return nil, true
			}
		}
		return value, present
	}
	if options.Basic {
		if value, present := lookup("associate_id"); present {
			if value == nil {
				invalid.Fields["associate_id"] = []string{PaymentCategoryValidationNull}
			} else if id, ok := personaDRFUUID(value); ok {
				fields.AssociateID = &id
			} else {
				invalid.Fields["associate_id"] = []string{PaymentCategoryValidationUUID}
			}
		}
	}
	if options.TutorsData {
		if value, present := lookup("tutors_data"); present {
			switch value.(type) {
			case nil:
				invalid.Fields["tutors_data"] = []string{PaymentCategoryValidationNull}
			case []any:
			case string:
				if !options.Form {
					invalid.Fields["tutors_data"] = []string{"not-a-list:str"}
				}
			default:
				invalid.Fields["tutors_data"] = []string{"not-a-list:" + pythonTypeName(value)}
			}
		}
	}
	for _, field := range PersonaTextFields {
		value, present := lookup(field.Name)
		if !present {
			continue
		}
		var parsed *string
		var codes []string
		if field.Name == "address_cap" {
			parsed, codes = associateText(field, value)
		} else {
			parsed, codes = modelTextValue(field, value, options.Form)
		}
		if len(codes) > 0 {
			invalid.Fields[field.Name] = codes
			continue
		}
		fields.Text[field.Name] = parsed
	}
	if value, present := lookup("sex"); present {
		switch v := value.(type) {
		case nil:
			invalid.Fields["sex"] = []string{PaymentCategoryValidationNull}
		case string:
			if v != "" && !associateSexChoices[v] {
				invalid.Fields["sex"] = []string{"choice:" + v}
			} else {
				fields.Sex = &v
			}
		default:
			invalid.Fields["sex"] = []string{"choice:" + PythonStr(value)}
		}
	}
	if value, present := lookup("born_date"); present {
		if value == nil {
			var none *time.Time
			fields.BornDate = &none
		} else if parsed, ok, err := ParseDMYDate(value); err != nil {
			return fields, nil, err
		} else if !ok {
			invalid.Fields["born_date"] = []string{PersonaDateFormat}
		} else {
			born := &parsed
			fields.BornDate = &born
		}
	}
	for _, name := range PersonaBooleanFields {
		value, present := lookup(name)
		if !present {
			if options.Form && !options.Partial {
				fields.Booleans[name] = false // BooleanField.default_empty_html
			}
			continue
		}
		flag, code := drfBoolean(value, false)
		if code != "" {
			invalid.Fields[name] = []string{code}
			continue
		}
		fields.Booleans[name] = *flag
	}
	if options.Basic {
		if value, present := lookup("creation_date"); present {
			if value == nil {
				invalid.Fields["creation_date"] = []string{PaymentCategoryValidationNull}
			} else if parsed, code := courseSubscriptionDateTime(value); code != "" {
				invalid.Fields["creation_date"] = []string{code}
			} else {
				fields.Created = parsed
			}
		}
	}
	for _, name := range PersonaRelationFields {
		value, present := lookup(name)
		if !present {
			continue
		}
		if value == nil {
			fields.Relations[name] = nil
			continue
		}
		id, code := supplierRelated(value)
		if code != "" {
			invalid.Fields[name] = []string{code}
			continue
		}
		exists, err := related(name, id)
		if err != nil {
			return fields, nil, err
		}
		if !exists {
			invalid.Fields[name] = []string{"related-does-not-exist:" + PythonStr(value)}
			continue
		}
		fields.Relations[name] = &id
	}
	if len(invalid.Fields) > 0 {
		return fields, invalid, nil
	}
	return fields, nil, nil
}

// personaDRFUUID is serializers.UUIDField.to_internal_value (hex_verbose):
// uuid.UUID(int=...) for ints, uuid.UUID(hex=...) for strings.
func personaDRFUUID(value any) (string, bool) {
	switch value.(type) {
	case string, bool:
		return PersonaUUIDValue(value)
	case json.Number:
		return PersonaUUIDValue(value)
	}
	return "", false
}

// ApplyPersonaFields sets the validated attributes on a row (ModelSerializer
// create/update setattr).
func ApplyPersonaFields(row *domain.PersonaAssociate, fields PersonaFields) {
	text := map[string]**string{"picture_path": &row.PicturePath, "first_name": &row.FirstName, "last_name": &row.LastName,
		"tax_code": &row.TaxCode, "born_city": &row.BornCity, "address_city": &row.AddressCity, "address": &row.Address,
		"address_cap": &row.AddressCap, "nationality": &row.Nationality, "nationality_residence": &row.NationalityResidence,
		"email": &row.Email, "phone": &row.Phone, "phone_2": &row.Phone2, "phone_3": &row.Phone3, "phone_4": &row.Phone4,
		"phone_label": &row.PhoneLabel, "phone_2_label": &row.Phone2Label, "phone_3_label": &row.Phone3Label,
		"phone_4_label": &row.Phone4Label, "family_role": &row.FamilyRole, "id_number": &row.IDNumber, "notes": &row.Notes}
	for name, value := range fields.Text {
		*text[name] = value
	}
	flags := map[string]*bool{"is_minor": &row.IsMinor, "disabled": &row.Disabled, "draft": &row.Draft, "deleted": &row.Deleted,
		"migrated": &row.Migrated}
	for name, value := range fields.Booleans {
		*flags[name] = value
	}
	if fields.Sex != nil {
		row.Sex = *fields.Sex
	}
	if fields.BornDate != nil {
		row.BornDate = *fields.BornDate
	}
	if fields.Created != nil {
		row.CreationDate = *fields.Created
	}
	relations := map[string]**string{"user": &row.UserID, "sport_association": &row.AssociationID, "group": &row.GroupID, "family": &row.FamilyID}
	for name, value := range fields.Relations {
		*relations[name] = value
	}
	if fields.AssociateID != nil {
		row.ID = *fields.AssociateID
	}
}

// PersonaChanges is auditlog's update diff of two Associate rows (str() of
// each concrete field; foreign keys by field name).
func PersonaChanges(old, row domain.PersonaAssociate) map[string][2]string {
	changes := map[string][2]string{}
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	date := func(value *time.Time) string {
		if value == nil {
			return "None"
		}
		return value.Format("2006-01-02")
	}
	flag := func(value bool) string {
		if value {
			return "True"
		}
		return "False"
	}
	diff("associate_id", old.ID, row.ID)
	diff("user", text(old.UserID), text(row.UserID))
	diff("sport_association", text(old.AssociationID), text(row.AssociationID))
	diff("picture_path", text(old.PicturePath), text(row.PicturePath))
	diff("first_name", text(old.FirstName), text(row.FirstName))
	diff("last_name", text(old.LastName), text(row.LastName))
	diff("sex", old.Sex, row.Sex)
	diff("tax_code", text(old.TaxCode), text(row.TaxCode))
	diff("born_date", date(old.BornDate), date(row.BornDate))
	diff("born_city", text(old.BornCity), text(row.BornCity))
	diff("address_city", text(old.AddressCity), text(row.AddressCity))
	diff("address", text(old.Address), text(row.Address))
	diff("address_cap", text(old.AddressCap), text(row.AddressCap))
	diff("nationality", text(old.Nationality), text(row.Nationality))
	diff("nationality_residence", text(old.NationalityResidence), text(row.NationalityResidence))
	diff("email", text(old.Email), text(row.Email))
	diff("phone", text(old.Phone), text(row.Phone))
	diff("phone_2", text(old.Phone2), text(row.Phone2))
	diff("phone_3", text(old.Phone3), text(row.Phone3))
	diff("phone_4", text(old.Phone4), text(row.Phone4))
	diff("phone_label", text(old.PhoneLabel), text(row.PhoneLabel))
	diff("phone_2_label", text(old.Phone2Label), text(row.Phone2Label))
	diff("phone_3_label", text(old.Phone3Label), text(row.Phone3Label))
	diff("phone_4_label", text(old.Phone4Label), text(row.Phone4Label))
	diff("is_minor", flag(old.IsMinor), flag(row.IsMinor))
	diff("disabled", flag(old.Disabled), flag(row.Disabled))
	diff("draft", flag(old.Draft), flag(row.Draft))
	diff("group", text(old.GroupID), text(row.GroupID))
	diff("deleted", flag(old.Deleted), flag(row.Deleted))
	diff("family", text(old.FamilyID), text(row.FamilyID))
	diff("family_role", text(old.FamilyRole), text(row.FamilyRole))
	diff("id_number", text(old.IDNumber), text(row.IDNumber))
	diff("creation_date", PersonaAuditMoment(old.CreationDate), PersonaAuditMoment(row.CreationDate))
	diff("migrated", flag(old.Migrated), flag(row.Migrated))
	diff("notes", text(old.Notes), text(row.Notes))
	return changes
}

// PersonaAuditMoment is str() of the naive UTC datetime auditlog compares.
func PersonaAuditMoment(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}
