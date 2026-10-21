package dto

import (
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

type LoginResponse struct {
	jwt.Pair
	Role            string           `json:"role"`
	UserData        LoginUserProfile `json:"user_data"`
	RequiresWelcome *bool            `json:"requires_welcome,omitempty"`
	TablesSettings  json.RawMessage  `json:"tables_settings,omitempty"`
}
type LoginUserProfile struct {
	UserAuthProfile
	IsSuperuser              *bool                    `json:"is_superuser,omitempty"`
	InstructorID             *string                  `json:"instructor_id,omitempty"`
	TemporaryInvoiceDeletion *bool                    `json:"temporary_invoice_deletion,omitempty"`
	PreviewAndCustomFeatures *[]RegistrationFeature   `json:"preview_and_custom_features,omitempty"`
	SportAssociation         *LoginAssociationProfile `json:"sport_association,omitempty"`
}
type LoginAssociationProfile struct {
	AssociationProfile
	EmptySections               *bool                        `json:"empty_sections,omitempty"`
	MembershipCardConfiguration *MembershipCardConfiguration `json:"membership_card_configuration"`
}
type RegistrationFeature struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}
