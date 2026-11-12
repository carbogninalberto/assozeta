package domain

import (
	"context"
	"errors"
	"time"
)

// ErrAssociationListNotes is user.sport_association failing for a listed row:
// several active associations for the notes owner (500).
var ErrAssociationListNotes = errors.New("association list notes owner is ambiguous")

type AssociationListOnboarding struct {
	ID                string `db:"users_onboarding_id" json:"users_onboarding_id"`
	Show              bool   `db:"show" json:"show"`
	CreateMembership  bool   `db:"create_membership" json:"create_membership"`
	ViewMembership    bool   `db:"view_membership" json:"view_membership"`
	ApprovePayment    bool   `db:"approve_payment" json:"approve_payment"`
	DownloadInvoice   bool   `db:"download_invoice" json:"download_invoice"`
	ViewCollaborators bool   `db:"view_collaborators" json:"view_collaborators"`
	ViewSettings      bool   `db:"view_settings" json:"view_settings"`
	User              string `db:"onboarding_user_id" json:"user"`
}

// AssociationListRow is one active association with its owner's columns,
// the notes resolved through User.sport_association, the single onboarding
// row (when exactly one exists) and the first billing subscription by key.
type AssociationListRow struct {
	AssociationID   string                     `db:"sport_association_id"`
	Denomination    string                     `db:"denomination"`
	TaxCode         string                     `db:"tax_code"`
	Address         *string                    `db:"address"`
	AddressCap      *string                    `db:"address_cap"`
	AddressCity     *string                    `db:"address_city"`
	UserID          string                     `db:"user_id"`
	Email           string                     `db:"email"`
	Username        string                     `db:"username"`
	FirstName       string                     `db:"first_name"`
	LastName        string                     `db:"last_name"`
	Phone           *string                    `db:"phone"`
	Role            *string                    `db:"lead_sport_association_role"`
	Size            *string                    `db:"lead_sport_association_size"`
	Market          *string                    `db:"lead_sport_market_channel"`
	DateJoined      time.Time                  `db:"date_joined"`
	LastLogin       *time.Time                 `db:"last_login"`
	OwnerRole       int                        `db:"owner_role"`
	NotesCount      int                        `db:"notes_count"`
	Notes           *string                    `db:"notes"`
	OnboardingCount int                        `db:"onboarding_count"`
	Onboarding      *AssociationListOnboarding `db:"-"`
	BillingPresent  bool                       `db:"billing_present"`
	AutoRenewal     *bool                      `db:"auto_renewal"`
	RenewalType     *int                       `db:"renewal_type"`
	EndsOn          *time.Time                 `db:"ends_on"`
	BillingPlan     *string                    `db:"billing_plan_id"`
}

type AssociationListStore interface {
	AssociationList(ctx context.Context, impersonationAssociation *string) ([]AssociationListRow, error)
}
