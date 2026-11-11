package dto

import (
	"encoding/json"
)

// ProfileIntegrationsInput is ProfileIntegrationsSerializer's validated_data
// (partial): only the supplied fields are set.
type ProfileIntegrationsInput struct {
	URLPresent     bool
	URL            *string
	EnabledPresent bool
	Enabled        bool
}

// profileIntegrationsTrue and profileIntegrationsFalse are the spellings DRF's
// BooleanField reads (exact, not case folded).
var profileIntegrationsTrue = map[string]bool{"t": true, "T": true, "y": true, "Y": true, "yes": true, "Yes": true, "YES": true,
	"true": true, "True": true, "TRUE": true, "on": true, "On": true, "ON": true, "1": true}
var profileIntegrationsFalse = map[string]bool{"f": true, "F": true, "n": true, "N": true, "no": true, "No": true, "NO": true,
	"false": true, "False": true, "FALSE": true, "off": true, "Off": true, "OFF": true, "0": true}

// ParseProfileIntegrations ports ProfileIntegrationsSerializer(data=request.data,
// partial=True).is_valid(): review_url is a nullable, blank-allowed CharField
// (255) and review_url_enabled a BooleanField. The field errors are codes for
// the shared message tables; form is an HTML input (QueryDict).
func ParseProfileIntegrations(body any, form bool) (ProfileIntegrationsInput, map[string]any) {
	var input ProfileIntegrationsInput
	object, invalid := drfSerializerData(body)
	if invalid != nil {
		return input, map[string]any{"non_field_errors": invalid[0].Codes}
	}
	errs := map[string]any{}
	if value, present := object.Lookup("review_url"); present {
		text, codes := modelTextValue(SupplierField{Name: "review_url", Max: 255, Null: true, Blank: true}, value, form)
		if len(codes) > 0 {
			errs["review_url"] = codes
		} else {
			input.URLPresent, input.URL = true, text
		}
	}
	if value, present := object.Lookup("review_url_enabled"); present {
		switch v := value.(type) {
		case nil:
			errs["review_url_enabled"] = []string{PaymentCategoryValidationNull}
		case bool:
			input.EnabledPresent, input.Enabled = true, v
		case string:
			if form && v == "" {
				// Field.get_value: an empty HTML value of a field that is not required is not sent.
				break
			}
			if profileIntegrationsTrue[v] {
				input.EnabledPresent, input.Enabled = true, true
			} else if profileIntegrationsFalse[v] {
				input.EnabledPresent, input.Enabled = true, false
			} else {
				errs["review_url_enabled"] = []string{PaymentCategoryValidationBoolean}
			}
		case json.Number:
			// 1 and 0 (and 1.0, 0.0, which compare equal) are in the sets.
			switch {
			case v.String() == "1" || isProfileIntegrationsFloat(v, 1):
				input.EnabledPresent, input.Enabled = true, true
			case v.String() == "0" || v.String() == "-0" || isProfileIntegrationsFloat(v, 0):
				input.EnabledPresent, input.Enabled = true, false
			default:
				errs["review_url_enabled"] = []string{PaymentCategoryValidationBoolean}
			}
		default:
			errs["review_url_enabled"] = []string{PaymentCategoryValidationBoolean}
		}
	}
	if len(errs) > 0 {
		return input, errs
	}
	return input, nil
}

func isProfileIntegrationsFloat(value json.Number, want float64) bool {
	number, err := value.Float64()
	return err == nil && number == want
}
