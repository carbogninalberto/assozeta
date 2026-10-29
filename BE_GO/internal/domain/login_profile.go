package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

// ErrProfileOnboardingUnavailable marks an optional read failure after the base
// snapshot was read successfully. The service owns the fallback response policy.
var ErrProfileOnboardingUnavailable = errors.New("profile onboarding unavailable")

// LoginProfileReader reads preferences for a trusted, already admitted account
// ID. It does not authorize an arbitrary public user/profile endpoint.
type LoginProfileReader interface {
	LoginProfile(context.Context, string) (LoginProfileSnapshot, error)
}

// LoginProfileSnapshot contains client-visible preferences and the metadata
// needed to decide welcome/onboarding. It never contains credentials.
type LoginProfileSnapshot struct {
	UserID                             string          `db:"user_id"`
	FirstName                          string          `db:"first_name"`
	LastName                           string          `db:"last_name"`
	Username                           string          `db:"username"`
	Email                              string          `db:"email"`
	AvatarImage                        *string         `db:"avatar_image"`
	CollaboratorRole                   int             `db:"collaborator_role"`
	CollaboratorPermissions            json.RawMessage `db:"collaborator_permissions"`
	GoogleSyncEnabled                  bool            `db:"google_sync_enabled"`
	DashboardLayout                    json.RawMessage `db:"dashboard_layout"`
	FullInstallmentsPlan               bool            `db:"full_installments_plan"`
	ShowZeroPayments                   bool            `db:"show_zero_payments"`
	DarkMode                           bool            `db:"dark_mode"`
	MedicalCertificateNotifications    bool            `db:"medical_certificate_notifications"`
	HideCategoryName                   bool            `db:"hide_category_name"`
	Phone                              *string         `db:"phone"`
	SubscriptionDurationEqualSportYear bool            `db:"subscription_duration_equal_sport_year"`
	BalanceSheetStartDay               int             `db:"balance_sheet_start_day"`
	BalanceSheetStartMonth             int             `db:"balance_sheet_start_month"`
	BalanceSheetYear                   int             `db:"balance_sheet_year"`
	SubscriptionDuration               int             `db:"subscription_duration"`
	MembershipDuration                 int             `db:"membership_duration"`
	MembershipStartingNumber           int             `db:"membership_starting_number"`
	DefaultMembershipType              *string         `db:"default_membership_type"`
	SubscriptionStartDay               int             `db:"subscription_start_day"`
	SubscriptionStartMonth             int             `db:"subscription_start_month"`
	CustomEndDate                      bool            `db:"custom_end_date"`
	SubscriptionEndDay                 int             `db:"subscription_end_day"`
	SubscriptionEndMonth               int             `db:"subscription_end_month"`
	LeadSportAssociationRole           *string         `db:"lead_sport_association_role"`
	LeadSportAssociationSize           *string         `db:"lead_sport_association_size"`
	LeadSportMarketChannel             *string         `db:"lead_sport_market_channel"`
	DefaultPaymentCategory             *string         `db:"default_payment_category"`
	DefaultPaymentCategoryCourses      *string         `db:"default_payment_category_courses"`
	Role                               int             `db:"role"`
	Active                             bool            `db:"is_active"`
	Deleted                            bool            `db:"deleted"`
	Superuser                          bool            `db:"is_superuser"`
	DateJoined                         time.Time       `db:"date_joined"`
	AssociationID                      *string         `db:"sport_association_id"`
	Logo                               *string         `db:"logo"`
	Denomination                       *string         `db:"denomination"`
	TaxCode                            *string         `db:"tax_code"`
	SetupProvenance                    *string         `db:"setup_provenance"`
	OnboardingCompletedAt              *time.Time      `db:"onboarding_completed_at"`
	Onboarding                         []ProfileOnboarding
}

type ProfileOnboarding struct {
	ID                string `db:"users_onboarding_id"`
	UserID            string `db:"user_id"`
	Show              bool   `db:"show"`
	CreateMembership  bool   `db:"create_membership"`
	ViewMembership    bool   `db:"view_membership"`
	ApprovePayment    bool   `db:"approve_payment"`
	DownloadInvoice   bool   `db:"download_invoice"`
	ViewCollaborators bool   `db:"view_collaborators"`
	ViewSettings      bool   `db:"view_settings"`
}
