package dto

import (
	"math/big"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type profileSettingKind int

const (
	profileSettingBoolean profileSettingKind = iota
	profileSettingChoice
	profileSettingInteger
	profileSettingText
	profileSettingRelation
)

// ProfileSettingsField is one UserSettingsSerializer field as ModelSerializer
// builds it from the User model field: BooleanField, ChoiceField (choices),
// IntegerField with the model's MaxValueValidator and integer range, a
// CharField(255, allow_null, allow_blank) or a PrimaryKeyRelatedField
// (allow_null). Every field has a model default, so none is required.
type ProfileSettingsField struct {
	Name     string
	kind     profileSettingKind
	min, max int64
	choices  int64 // choices are 1..choices
}

// ProfileSettingsFields is the serializer's field order (Meta.fields).
var ProfileSettingsFields = []ProfileSettingsField{
	{Name: "enumerate_invoices"}, {Name: "online_payments"},
	{Name: "balance_sheet_year", kind: profileSettingChoice, choices: 4},
	{Name: "balance_sheet_start_day", kind: profileSettingInteger, max: 31},
	{Name: "balance_sheet_start_month", kind: profileSettingInteger, max: 12},
	{Name: "temporary_invoice_deletion"}, {Name: "auto_archive"}, {Name: "auto_mark_attendance"},
	{Name: "payment_date_equal_invoice_date"},
	{Name: "starting_number_invoices", kind: profileSettingInteger, min: -32768, max: 32767},
	{Name: "auto_paid_payment"}, {Name: "full_installments_plan"}, {Name: "show_zero_payments"}, {Name: "dark_mode"},
	{Name: "medical_certificate_notifications"}, {Name: "hide_category_name"}, {Name: "subscription_duration_equal_sport_year"},
	{Name: "subscription_duration", kind: profileSettingChoice, choices: 5},
	{Name: "membership_duration", kind: profileSettingChoice, choices: 5},
	{Name: "membership_starting_number", kind: profileSettingInteger, min: -2147483648, max: 2147483647},
	{Name: "default_membership_type", kind: profileSettingText},
	{Name: "subscription_start_day", kind: profileSettingInteger, max: 31},
	{Name: "subscription_start_month", kind: profileSettingInteger, max: 12},
	{Name: "custom_end_date"},
	{Name: "subscription_end_day", kind: profileSettingInteger, max: 31},
	{Name: "subscription_end_month", kind: profileSettingInteger, max: 12},
	{Name: "disable_account_creation"}, {Name: "force_account_creation"},
	{Name: "default_payment_category", kind: profileSettingRelation},
	{Name: "default_payment_category_courses", kind: profileSettingRelation},
}

// ProfileSettingsRelation is a PrimaryKeyRelatedField value before the
// queryset lookup: None, or a canonical UUID with the input's str() for the
// does_not_exist message.
type ProfileSettingsRelation struct {
	Null    bool
	ID, Raw string
}

// ProfileSettingsInput is validated_data before the relation lookups: bool,
// int64, *string (None is nil) or ProfileSettingsRelation per present field.
type ProfileSettingsInput map[string]any

// ProfileSettingsUploadUnsupported marks an uploaded file in the request
// data (multipart); the port refuses it before any write.
type ProfileSettingsUploadUnsupported struct{}

func (ProfileSettingsUploadUnsupported) Error() string {
	return "profile settings upload is not ported"
}

// ProfileSettingsHasUpload reports whether the parsed body carries a spooled file.
func ProfileSettingsHasUpload(body any) bool {
	switch value := body.(type) {
	case domain.RequestUpload:
		// Only a spooled file (TemporaryUploadedFile) is refused; an in-memory one is a QueryDict value.
		return value.Temp
	case domain.RequestObject:
		for _, field := range value {
			if ProfileSettingsHasUpload(field.Value) {
				return true
			}
		}
	case []any:
		for _, item := range value {
			if ProfileSettingsHasUpload(item) {
				return true
			}
		}
	}
	return false
}

// ParseProfileSettings ports UserSettingsSerializer(data=request.data)
// field validation (codes rendered by the payment-category message table and
// "max-value:N"). Relation existence and tenant scope are the service's.
func ParseProfileSettings(body any, form bool) (ProfileSettingsInput, map[string]any) {
	if body == nil {
		return nil, map[string]any{"non_field_errors": []string{"no-data"}}
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}
	}
	input, invalid := ProfileSettingsInput{}, map[string]any{}
	for _, field := range ProfileSettingsFields {
		value, present := object.Lookup(field.Name)
		if form {
			// Field.get_value for HTML input: an absent BooleanField is False
			// (default_empty_html); an empty value is None for nullable fields
			// (the CharField allows blank and keeps ''), otherwise empty.
			nullable := field.kind == profileSettingText || field.kind == profileSettingRelation
			switch {
			case !present && field.kind == profileSettingBoolean:
				value, present = false, true
			case present && value == "" && field.kind == profileSettingRelation:
				value = nil
			case present && value == "" && !nullable:
				present = false
			}
		}
		if !present {
			continue
		}
		parsed, codes := parseProfileSetting(field, value, form)
		if len(codes) > 0 {
			invalid[field.Name] = codes
			continue
		}
		input[field.Name] = parsed
	}
	if len(invalid) > 0 {
		return input, invalid
	}
	return input, nil
}

func parseProfileSetting(field ProfileSettingsField, value any, form bool) (any, []string) {
	if field.kind == profileSettingRelation && value == "" {
		// RelatedField.run_validation forces '' to None.
		value = nil
	}
	if upload, ok := value.(domain.RequestUpload); ok {
		// A file part: str() is its name; the typed fields reject it like any other object.
		switch field.kind {
		case profileSettingBoolean:
			return nil, []string{PaymentCategoryValidationBoolean}
		case profileSettingChoice:
			return nil, []string{"choice:" + upload.Name}
		case profileSettingInteger:
			return nil, []string{DRFValidationInteger}
		case profileSettingText:
			return nil, []string{PaymentCategoryValidationString}
		}
	}
	if value == nil {
		switch field.kind {
		case profileSettingText:
			return (*string)(nil), nil
		case profileSettingRelation:
			return ProfileSettingsRelation{Null: true}, nil
		}
		return nil, []string{PaymentCategoryValidationNull}
	}
	switch field.kind {
	case profileSettingBoolean:
		parsed, err := parsePaymentCategoryBoolean(value)
		if err != nil {
			return nil, []string{PaymentCategoryValidationBoolean}
		}
		return parsed, nil
	case profileSettingChoice:
		text := PythonStr(value)
		choice, err := strconv.ParseInt(text, 10, 64)
		if err != nil || choice < 1 || choice > field.choices || strconv.FormatInt(choice, 10) != text {
			return nil, []string{"choice:" + text}
		}
		return choice, nil
	case profileSettingInteger:
		number, code := DRFInteger(value)
		if code != "" {
			return nil, []string{code}
		}
		if number.Cmp(big.NewInt(field.max)) > 0 {
			return nil, []string{"max-value:" + strconv.FormatInt(field.max, 10)}
		}
		if number.Cmp(big.NewInt(field.min)) < 0 {
			return nil, []string{"min-value:" + strconv.FormatInt(field.min, 10)}
		}
		return number.Int64(), nil
	case profileSettingText:
		text, codes := modelTextValue(SupplierField{Name: field.Name, Max: 255, Null: true, Blank: true}, value, form)
		if len(codes) > 0 {
			return nil, codes
		}
		return text, nil
	}
	id, code := profileSettingsRelated(value)
	if code != "" {
		return nil, []string{code}
	}
	return ProfileSettingsRelation{ID: id, Raw: PythonStr(value)}, nil
}

// profileSettingsRelated is supplierRelated (PrimaryKeyRelatedField on a UUID key).
func profileSettingsRelated(value any) (string, string) {
	return supplierRelated(value)
}
