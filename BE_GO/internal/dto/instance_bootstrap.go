package dto

type InstanceStatus struct {
	AIEnabled         any      `json:"ai_enabled"`
	ManualEnabled     bool     `json:"manual_enabled"`
	Configured        bool     `json:"configured"`
	Version           string   `json:"version"`
	InstanceName      *string  `json:"instance_name"`
	SupportedFeatures []string `json:"supported_features"`
}
type InstanceOEM struct {
	Name            string         `json:"name"`
	Abbreviation    string         `json:"abbreviation"`
	Logo            string         `json:"logo"`
	SupportEmail    string         `json:"supportEmail"`
	PrimaryColor    string         `json:"primaryColor"`
	DisplaySettings map[string]any `json:"displaySettings"`
	ManualURL       string         `json:"manualUrl"`
}
type InstanceOAuth struct {
	GoogleClientID any `json:"googleClientId"`
	AppleClientID  any `json:"appleClientId"`
	GoogleEnabled  any `json:"googleEnabled"`
}
type InstanceStripe struct {
	PublicKey    any `json:"publicKey"`
	PricingTable any `json:"pricingTable"`
	ClientPortal any `json:"clientPortal"`
}
type InstanceMeta struct {
	Title       string `json:"title"`
	Description string `json:"description"`
	Manifest    string `json:"manifest"`
}
type InstanceFeatures struct {
	AIEnabled                   any  `json:"aiEnabled"`
	ManualEnabled               bool `json:"manualEnabled"`
	IsReseller                  bool `json:"isReseller"`
	SelfHosted                  bool `json:"selfHosted"`
	SupportMultipleAssociations bool `json:"supportMultipleAssociations"`
	ShowCourseAthletes          bool `json:"showCourseAthletes"`
	ShowMinorExemption          bool `json:"showMinorExemption"`
	ShowTutorial                bool `json:"showTutorial"`
	FamilyRegistration          bool `json:"familyRegistration"`
	FederationRegistration      bool `json:"federationRegistration"`
	MultipleRegistration        bool `json:"multipleRegistration"`
}
type InstanceSetup struct {
	Provenance         string `json:"provenance"`
	OnboardingComplete bool   `json:"onboardingComplete"`
}
type InstanceConfig struct {
	OEM      InstanceOEM      `json:"oem"`
	OAuth    InstanceOAuth    `json:"oauth"`
	Stripe   InstanceStripe   `json:"stripe"`
	Meta     InstanceMeta     `json:"meta"`
	Features InstanceFeatures `json:"features"`
	Setup    InstanceSetup    `json:"setup"`
}
type InstanceDataAssociation struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}
type InstanceAccess struct {
	IsOwner         bool                     `json:"is_owner"`
	IsAdministrator bool                     `json:"is_administrator"`
	DataAssociation *InstanceDataAssociation `json:"data_association"`
}
