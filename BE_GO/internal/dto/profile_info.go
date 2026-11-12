package dto

type ProfileInfo struct {
	Info     ProfileInfoRole `json:"info"`
	UserData ProfileInfoUser `json:"user_data"`
}
type ProfileInfoRole struct {
	Role string `json:"role"`
}
type ProfileInfoUser struct {
	UserAuthProfile
	IsSuperuser              *bool                   `json:"is_superuser,omitempty"`
	TemporaryInvoiceDeletion *bool                   `json:"temporary_invoice_deletion,omitempty"`
	PreviewAndCustomFeatures *[]RegistrationFeature  `json:"preview_and_custom_features,omitempty"`
	SportAssociation         *ProfileInfoAssociation `json:"sport_association,omitempty"`
}
type ProfileInfoAssociation struct {
	AssociationProfile
	EmptySections    *bool   `json:"empty_sections,omitempty"`
	ReviewURL        *string `json:"review_url"`
	ReviewURLEnabled bool    `json:"review_url_enabled"`
}
