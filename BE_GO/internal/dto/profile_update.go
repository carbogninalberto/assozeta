package dto

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrProfileUpdateShape = errors.New("invalid profile update input")

type ProfileUpdateRequest struct {
	Person      map[string]any
	Association any
}

type ProfileUpdateResponse struct {
	Role     string            `json:"role"`
	UserData ProfileUpdateUser `json:"user_data"`
}
type ProfileUpdateUser struct {
	UserAuthProfile
	SportAssociation         *AssociationProfile    `json:"sport_association,omitempty"`
	PreviewAndCustomFeatures *[]RegistrationFeature `json:"preview_and_custom_features,omitempty"`
}

// Django reads the serializer's initial_data without calling is_valid. Keep
// required presence separate from null, and leave model conversion to services.
func ParseProfileUpdate(body any) (ProfileUpdateRequest, error) {
	empty := ProfileUpdateRequest{}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return empty, ErrProfileUpdateShape
	}
	nested, ok := object.Lookup("user_data")
	if !ok {
		return empty, ErrProfileUpdateShape
	}
	data, ok := nested.(domain.RequestObject)
	if !ok {
		return empty, ErrProfileUpdateShape
	}
	person := make(map[string]any, 4)
	for _, key := range []string{"first_name", "last_name", "username", "avatar_image"} {
		value, found := data.Lookup(key)
		if !found {
			return empty, ErrProfileUpdateShape
		}
		if value != nil {
			person[key] = value
		}
	}
	association, ok := data.Lookup("sport_association")
	if !ok {
		return empty, ErrProfileUpdateShape
	}
	return ProfileUpdateRequest{Person: person, Association: association}, nil
}

// Only an association owner's service invokes this parser. Other roles ignore
// the nested value entirely, including malformed association shapes.
func ParseProfileAssociationUpdate(input any) (map[string]any, error) {
	data, ok := input.(domain.RequestObject)
	if !ok {
		return nil, ErrProfileUpdateShape
	}
	fields := make(map[string]any)
	for _, key := range []string{"denomination", "tax_code", "address", "address_cap", "address_city", "enable_quotes_management", "configuration", "federation", "enroll_number", "sport", "president_first_name", "president_last_name", "stripe_available_methods", "invoice_template", "subscription_template", "extra_text_invoices", "checkout_info"} {
		value, found := data.Lookup(key)
		if !found {
			return nil, ErrProfileUpdateShape
		}
		if value != nil || key == "checkout_info" {
			fields[key] = value
		}
	}
	for _, key := range []string{"document_header", "invoice_footer", "president_signature", "stamp"} {
		if value, found := data.Lookup(key); found {
			fields[key] = value
		}
	}
	for _, key := range []string{"iban", "abbreviated", "vat_number", "website", "whatsapp"} {
		if value, found := data.Lookup(key); found && value != nil {
			fields[key] = value
		}
	}
	return fields, nil
}
