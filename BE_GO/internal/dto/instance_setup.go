package dto

import (
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Serializer ports for BE/instance/serializers.py InstanceSetupSerializer
// (first-run configure) and InstanceReconfigureSerializer (partial=True).
// Errors use the instance administration render codes; literal messages
// raised by the serializers' own validators are their own codes.

// InstanceSetupInvalid is serializer.errors before rendering; the views
// answer 400 {"success": False, "errors": ...}.
type InstanceSetupInvalid struct{ Errors any }

func (e *InstanceSetupInvalid) Error() string { return "instance setup validation failed" }

// InstanceSetupInput is InstanceSetupSerializer.validated_data as the view
// reads it ("or ”" applied).
type InstanceSetupInput struct {
	Domain                                                 string
	DomainASCII                                            bool
	Name, Abbreviation, PrimaryColor, SupportEmail, Logo   string
	GoogleClientID, AppleClientID                          string
	Fresh                                                  bool
	AssociationName, OwnerEmail, OwnerPassword, ImportTask string
	// RefusedURL marks a URLField value that needs URLValidator's IDNA fallback.
	RefusedURL bool
}

type setupField struct {
	name     string
	field    drfText
	required bool
	// fallback is the field default (not validated), applied when omitted
	// outside a partial update.
	fallback *string
}

func setupDefault(value string) *string { return &value }

// runSetupFields is Serializer.to_internal_value over CharField-like fields:
// the validated values of present (or defaulted) fields and the field errors.
func runSetupFields(object domain.RequestObject, specs []setupField, partial bool) (map[string]string, map[string]any) {
	values, errs := map[string]string{}, map[string]any{}
	for _, spec := range specs {
		value, present := object.Lookup(spec.name)
		if !present {
			switch {
			case partial:
			case spec.required:
				errs[spec.name] = []string{PaymentCategoryValidationRequired}
			case spec.fallback != nil:
				values[spec.name] = *spec.fallback
			}
			continue
		}
		text, codes := spec.field.run(value)
		if codes != nil {
			errs[spec.name] = codes
			continue
		}
		values[spec.name] = text
	}
	return values, errs
}

// setupNested is a nested Serializer field's empty-value and type checks:
// present=false when the field is omitted without an error.
func setupNested(object domain.RequestObject, key string, required bool) (domain.RequestObject, bool, any) {
	value, present := object.Lookup(key)
	if !present {
		if required {
			return nil, false, []string{PaymentCategoryValidationRequired}
		}
		return nil, false, nil
	}
	if value == nil {
		return nil, false, []string{PaymentCategoryValidationNull}
	}
	nested, ok := value.(domain.RequestObject)
	if !ok {
		return nil, false, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(value)}}
	}
	return nested, true, nil
}

func setupEmail(text string) []string {
	if !SupplierEmail(text) {
		return []string{SupplierValidationEmail}
	}
	return nil
}

func setupColor(text string) []string {
	if !instanceAdminColor.MatchString(text) {
		return []string{InstanceAdminRegex}
	}
	return nil
}

// setupOEM is OEMInputSerializer (and OEMSetupInputSerializer with logo).
func setupOEM(withLogo, partial bool) []setupField {
	fields := []setupField{
		{name: "name", field: drfText{max: 255}, required: true},
		{name: "abbreviation", field: drfText{max: 50, allowBlank: true}, fallback: setupDefault("")},
		{name: "primaryColor", field: drfText{invalid: InstanceAdminRegex, validators: []func(string) []string{setupColor}}, fallback: setupDefault("#351DC2")},
		{name: "supportEmail", field: drfText{allowBlank: true, invalid: SupplierValidationEmail, validators: []func(string) []string{setupEmail}}, fallback: setupDefault("")},
	}
	if withLogo {
		fields = append(fields, setupField{name: "logo", field: drfText{max: 255, allowBlank: true, allowNull: true}})
	}
	return fields
}

var setupOAuth = []setupField{
	{name: "googleClientId", field: drfText{max: 255, allowBlank: true, allowNull: true}, fallback: setupDefault("")},
	{name: "appleClientId", field: drfText{max: 255, allowBlank: true, allowNull: true}, fallback: setupDefault("")},
}

func setupStripe(refused *bool) []setupField {
	url := func(text string) []string {
		valid, ascii := DjangoURL(text)
		if !ascii {
			*refused = true
		}
		if !valid {
			return []string{InstanceAdminURL}
		}
		return nil
	}
	return []setupField{
		{name: "publicKey", field: drfText{max: 255, allowBlank: true}, fallback: setupDefault("")},
		{name: "secretKey", field: drfText{max: 255, allowBlank: true}, fallback: setupDefault("")},
		{name: "webhookSecret", field: drfText{max: 255, allowBlank: true}, fallback: setupDefault("")},
		{name: "pricingTable", field: drfText{max: 255, allowBlank: true}, fallback: setupDefault("")},
		{name: "clientPortal", field: drfText{allowBlank: true, invalid: InstanceAdminURL, validators: []func(string) []string{url}}, fallback: setupDefault("")},
	}
}

const canonicalInstanceLogo = "/api/instance/logo.png"

// InstanceSetupData is InstanceSetupSerializer(data=request.data).is_valid().
func InstanceSetupData(body any) (*InstanceSetupInput, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, &InstanceSetupInvalid{Errors: map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}}
	}
	input := &InstanceSetupInput{}
	errs := map[string]any{}
	// domain: CharField(max_length=255), then validate_domain.
	domainValues, domainErrors := runSetupFields(object, []setupField{{name: "domain", field: drfText{max: 255}, required: true}}, false)
	if codes, failed := domainErrors["domain"]; failed {
		errs["domain"] = codes
	} else if text := domainValues["domain"]; text == "" || utf8.RuneCountInString(text) < 3 {
		errs["domain"] = []string{"Domain must be at least 3 characters."}
	} else {
		input.DomainASCII = true
		for _, r := range text {
			if r > 127 {
				input.DomainASCII = false
			}
		}
		input.Domain = pythonStrip(strings.ToLower(text))
	}
	// oem: OEMSetupInputSerializer, then validate_logo.
	if nested, present, failure := setupNested(object, "oem", true); failure != nil {
		errs["oem"] = failure
	} else if present {
		values, nestedErrors := runSetupFields(nested, setupOEM(true, false), false)
		if logo, given := values["logo"]; given && logo != "" && logo != canonicalInstanceLogo {
			nestedErrors["logo"] = []string{"Logo must be " + canonicalInstanceLogo + "."}
		}
		if len(nestedErrors) > 0 {
			errs["oem"] = nestedErrors
		} else {
			input.Name, input.Abbreviation, input.PrimaryColor = values["name"], values["abbreviation"], values["primaryColor"]
			input.SupportEmail, input.Logo = values["supportEmail"], values["logo"]
		}
	}
	// oauth / stripe: required=False, default=dict (the default is not validated).
	if nested, present, failure := setupNested(object, "oauth", false); failure != nil {
		errs["oauth"] = failure
	} else if present {
		values, nestedErrors := runSetupFields(nested, setupOAuth, false)
		if len(nestedErrors) > 0 {
			errs["oauth"] = nestedErrors
		} else {
			input.GoogleClientID, input.AppleClientID = values["googleClientId"], values["appleClientId"]
		}
	}
	if nested, present, failure := setupNested(object, "stripe", false); failure != nil {
		errs["stripe"] = failure
	} else if present {
		if _, nestedErrors := runSetupFields(nested, setupStripe(&input.RefusedURL), false); len(nestedErrors) > 0 {
			errs["stripe"] = nestedErrors
		}
	}
	// initialization: InitializationInputSerializer, its validate() only
	// after its fields validate.
	if nested, present, failure := setupNested(object, "initialization", true); failure != nil {
		errs["initialization"] = failure
	} else if present {
		if failure := setupInitialization(nested, input); failure != nil {
			errs["initialization"] = failure
		}
	}
	if len(errs) > 0 {
		return nil, &InstanceSetupInvalid{Errors: errs}
	}
	return input, nil
}

func setupInitialization(object domain.RequestObject, input *InstanceSetupInput) any {
	errs := map[string]any{}
	kind := ""
	if value, present := object.Lookup("type"); !present {
		errs["type"] = []string{PaymentCategoryValidationRequired}
	} else if choice, codes := drfChoiceField(value, "fresh", "import"); codes != nil {
		errs["type"] = codes
	} else {
		kind = choice
	}
	values, fieldErrors := runSetupFields(object, []setupField{
		{name: "associationName", field: drfText{max: 150}},
		{name: "ownerEmail", field: drfText{invalid: SupplierValidationEmail, validators: []func(string) []string{setupEmail}}},
		{name: "ownerPassword", field: drfText{max: 128}},
		{name: "importTaskId", field: drfText{max: 255}},
	}, false)
	for key, codes := range fieldErrors {
		errs[key] = codes
	}
	if len(errs) > 0 {
		return errs
	}
	single := func(key, message string) any { return map[string]any{key: []string{message}} }
	switch kind {
	case "fresh":
		for _, key := range []string{"associationName", "ownerEmail", "ownerPassword"} {
			if values[key] == "" {
				return single(key, "Required for fresh initialization.")
			}
		}
		if utf8.RuneCountInString(values["ownerPassword"]) < 8 {
			return single("ownerPassword", "Password must be at least 8 characters.")
		}
		input.Fresh = true
	case "import":
		if values["importTaskId"] == "" {
			return single("importTaskId", "Required for import initialization.")
		}
	}
	input.AssociationName, input.OwnerEmail, input.OwnerPassword = values["associationName"], values["ownerEmail"], values["ownerPassword"]
	input.ImportTask = values["importTaskId"]
	return nil
}

// InstanceReconfigure is InstanceReconfigureSerializer.update's write set.
type InstanceReconfigure struct {
	// Columns are the edited model columns (text and boolean values);
	// DisplaySettings is the JSON to store when display_settings was given.
	Columns         map[string]any
	DisplaySettings any
	HasDisplay      bool
	RefusedURL      bool
}

// InstanceReconfigureInput is InstanceReconfigureSerializer(config,
// data=request.data, partial=True).is_valid() and the columns update()
// assigns (updated_at is always saved).
func InstanceReconfigureInput(body any) (*InstanceReconfigure, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, &InstanceSetupInvalid{Errors: map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}}
	}
	result := &InstanceReconfigure{Columns: map[string]any{}}
	errs := map[string]any{}
	if nested, present, failure := setupNested(object, "oem", false); failure != nil {
		errs["oem"] = failure
	} else if present {
		values, nestedErrors := runSetupFields(nested, setupOEM(false, true), true)
		if len(nestedErrors) > 0 {
			errs["oem"] = nestedErrors
		} else {
			for key, column := range map[string]string{"name": "name", "abbreviation": "abbreviation", "primaryColor": "primary_color", "supportEmail": "support_email"} {
				if value, given := values[key]; given {
					result.Columns[column] = value
				}
			}
		}
	}
	if nested, present, failure := setupNested(object, "oauth", false); failure != nil {
		errs["oauth"] = failure
	} else if present {
		values, nestedErrors := runSetupFields(nested, setupOAuth, true)
		if len(nestedErrors) > 0 {
			errs["oauth"] = nestedErrors
		} else if len(values) > 0 {
			// `if oauth_data:` then both ids, a missing one as ''.
			result.Columns["google_client_id"], result.Columns["apple_client_id"] = values["googleClientId"], values["appleClientId"]
		}
	}
	if nested, present, failure := setupNested(object, "stripe", false); failure != nil {
		errs["stripe"] = failure
	} else if present {
		// Validated, never written: Stripe credentials are environment-owned.
		if _, nestedErrors := runSetupFields(nested, setupStripe(&result.RefusedURL), true); len(nestedErrors) > 0 {
			errs["stripe"] = nestedErrors
		}
	}
	if value, present := object.Lookup("display_settings"); present {
		if value == nil {
			errs["display_settings"] = []string{PaymentCategoryValidationNull}
		} else {
			result.DisplaySettings, result.HasDisplay = value, true
		}
	}
	texts, textErrors := runSetupFields(object, []setupField{
		{name: "meta_title", field: drfText{max: 255, allowBlank: true}},
		{name: "meta_description", field: drfText{allowBlank: true}},
	}, true)
	for key, codes := range textErrors {
		errs[key] = codes
	}
	for key, value := range texts {
		result.Columns[key] = value
	}
	for _, key := range []string{"show_course_athletes", "show_minor_exemption", "show_tutorial"} {
		value, present := object.Lookup(key)
		if !present {
			continue
		}
		flag, codes := drfBooleanField(value)
		if codes != nil {
			errs[key] = codes
			continue
		}
		result.Columns[key] = flag
	}
	if len(errs) > 0 {
		return nil, &InstanceSetupInvalid{Errors: errs}
	}
	return result, nil
}
