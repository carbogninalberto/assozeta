package domain

import (
	"context"
	"encoding/json"
	"time"
)

// AssociationProfileReader accepts an owner selected by the trusted login flow.
// It is serialization access, not authorization for a public profile endpoint.
type AssociationProfileReader interface {
	AssociationProfile(context.Context, string) (AssociationProfileSnapshot, error)
}

// Fees are exact stored numeric text for serialization, never floating point.
// Financial arithmetic/writes require their own decimal-domain implementation.
type AssociationProfileSnapshot struct {
	ID                         string          `db:"sport_association_id"`
	Denomination               string          `db:"denomination"`
	TaxCode                    string          `db:"tax_code"`
	Address                    *string         `db:"address"`
	AddressCAP                 *string         `db:"address_cap"`
	AddressCity                *string         `db:"address_city"`
	Regulation                 *string         `db:"regulation"`
	Demand                     *string         `db:"demand"`
	Logo                       *string         `db:"logo"`
	SubscriptionFee            string          `db:"subscription_fee"`
	MultipleSubscriptionFee    bool            `db:"multiple_subscription_fee"`
	SubscriptionFeePlans       json.RawMessage `db:"subscription_fee_plans"`
	MembershipFee              *string         `db:"membership_fee"`
	MultipleMembershipFee      bool            `db:"multiple_membership_fee"`
	MembershipFeePlans         json.RawMessage `db:"membership_fee_plans"`
	CustomSubscriptionFormData json.RawMessage `db:"custom_subscription_form_data"`
	DocumentHeader             *string         `db:"document_header"`
	InvoiceFooter              *string         `db:"invoice_footer"`
	EnableQuotesManagement     bool            `db:"enable_quotes_management"`
	Configuration              json.RawMessage `db:"configuration"`
	AffiliateCode              *string         `db:"affiliate_code"`
	AdditionalSections         json.RawMessage `db:"additional_sections"`
	PresidentSignature         *string         `db:"president_signature"`
	Stamp                      *string         `db:"stamp"`
	PresidentFirstName         *string         `db:"president_first_name"`
	PresidentLastName          *string         `db:"president_last_name"`
	Federation                 *string         `db:"federation"`
	EnrollNumber               *string         `db:"enroll_number"`
	Sport                      *string         `db:"sport"`
	EnabledFor                 json.RawMessage `db:"enabled_for"`
	CheckoutInfo               *string         `db:"checkout_info"`
	AdditionalFields           json.RawMessage `db:"additional_fields"`
	StripeAvailableMethods     json.RawMessage `db:"stripe_available_methods"`
	IBAN                       *string         `db:"iban"`
	Website                    *string         `db:"website"`
	Abbreviated                *string         `db:"abbreviated"`
	VATNumber                  *string         `db:"vat_number"`
	WhatsApp                   *string         `db:"whatsapp"`
	InvoiceTemplate            *string         `db:"invoice_template"`
	SubscriptionTemplate       *string         `db:"subscription_template"`
	ExtraTextInvoices          *string         `db:"extra_text_invoices"`
	ImportedFromAssociami      bool            `db:"imported_from_associami"`
	ShowRegulationToMembers    bool            `db:"show_regulation_to_members"`
	ShowRegulationToBoth       bool            `db:"show_regulation_to_both"`
	ShowRegulationToAthletes   bool            `db:"show_regulation_to_athletes"`
	ShowDemandToMembers        bool            `db:"show_demand_to_members"`
	ShowDemandToBoth           bool            `db:"show_demand_to_both"`
	ShowDemandToAthletes       bool            `db:"show_demand_to_athletes"`
	OwnerID                    string          `db:"user_id"`
	OwnerJoined                time.Time       `db:"date_joined"`
	Reviewed                   bool            `db:"reviewed"`
	Groups                     []AssociationProfileGroup
}

type AssociationProfileGroup struct {
	ID            string  `db:"group_id"`
	Name          string  `db:"name"`
	Description   *string `db:"description"`
	AssociationID string  `db:"sport_association_id"`
}
