package dto

import "encoding/json"

// AssociationProfile is the base SportAssociationSerializer wire contract.
// Image processing and membership-card configuration augmentation are separate.
type AssociationProfile struct {
	ID                         string                    `json:"sport_association_id"`
	Denomination               string                    `json:"denomination"`
	TaxCode                    string                    `json:"tax_code"`
	Address                    *string                   `json:"address"`
	AddressCAP                 *string                   `json:"address_cap"`
	AddressCity                *string                   `json:"address_city"`
	Regulation                 *string                   `json:"regulation"`
	Demand                     *string                   `json:"demand"`
	Logo                       *string                   `json:"logo"`
	SubscriptionFee            string                    `json:"subscription_fee"`
	MultipleSubscriptionFee    bool                      `json:"multiple_subscription_fee"`
	SubscriptionFeePlans       json.RawMessage           `json:"subscription_fee_plans"`
	MembershipFee              *string                   `json:"membership_fee"`
	MultipleMembershipFee      bool                      `json:"multiple_membership_fee"`
	MembershipFeePlans         json.RawMessage           `json:"membership_fee_plans"`
	CustomSubscriptionFormData json.RawMessage           `json:"custom_subscription_form_data"`
	DocumentHeader             *string                   `json:"document_header"`
	InvoiceFooter              *string                   `json:"invoice_footer"`
	EnableQuotesManagement     bool                      `json:"enable_quotes_management"`
	Configuration              json.RawMessage           `json:"configuration"`
	AffiliateCode              *string                   `json:"affiliate_code"`
	AdditionalSections         json.RawMessage           `json:"additional_sections"`
	PresidentSignature         *string                   `json:"president_signature"`
	Stamp                      *string                   `json:"stamp"`
	PresidentFirstName         *string                   `json:"president_first_name"`
	PresidentLastName          *string                   `json:"president_last_name"`
	Federation                 *string                   `json:"federation"`
	EnrollNumber               *string                   `json:"enroll_number"`
	Sport                      *string                   `json:"sport"`
	EnabledFor                 json.RawMessage           `json:"enabled_for"`
	CheckoutInfo               *string                   `json:"checkout_info"`
	AdditionalFields           json.RawMessage           `json:"additional_fields"`
	StripeAvailableMethods     json.RawMessage           `json:"stripe_available_methods"`
	IBAN                       *string                   `json:"iban"`
	Website                    *string                   `json:"website"`
	Abbreviated                *string                   `json:"abbreviated"`
	VATNumber                  *string                   `json:"vat_number"`
	WhatsApp                   *string                   `json:"whatsapp"`
	InvoiceTemplate            *string                   `json:"invoice_template"`
	SubscriptionTemplate       *string                   `json:"subscription_template"`
	ExtraTextInvoices          *string                   `json:"extra_text_invoices"`
	ImportedFromAssociami      bool                      `json:"imported_from_associami"`
	ShowRegulationToMembers    bool                      `json:"show_regulation_to_members"`
	ShowRegulationToBoth       bool                      `json:"show_regulation_to_both"`
	ShowRegulationToAthletes   bool                      `json:"show_regulation_to_athletes"`
	ShowDemandToMembers        bool                      `json:"show_demand_to_members"`
	ShowDemandToBoth           bool                      `json:"show_demand_to_both"`
	ShowDemandToAthletes       bool                      `json:"show_demand_to_athletes"`
	Reviewed                   bool                      `json:"reviewed"`
	Groups                     []AssociationProfileGroup `json:"groups"`
}

type AssociationProfileGroup struct {
	Label string `json:"label"`
	Value string `json:"value"`
}
