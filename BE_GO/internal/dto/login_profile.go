package dto

import "encoding/json"

// UserAuthProfile is the base Django UserAuthSerializer contract. Association
// augmentation, image compression and top-level login response are separate.
// Null fields are intentional; do not use omitempty.
type UserAuthProfile struct {
	UserID                             string          `json:"user_id"`
	FirstName                          string          `json:"first_name"`
	LastName                           string          `json:"last_name"`
	Username                           string          `json:"username"`
	Email                              string          `json:"email"`
	AvatarImage                        *string         `json:"avatar_image"`
	CollaboratorRole                   int             `json:"collaborator_role"`
	CollaboratorPermissions            json.RawMessage `json:"collaborator_permissions"`
	GoogleSyncEnabled                  bool            `json:"google_sync_enabled"`
	DashboardLayout                    json.RawMessage `json:"dashboard_layout"`
	FullInstallmentsPlan               bool            `json:"full_installments_plan"`
	ShowZeroPayments                   bool            `json:"show_zero_payments"`
	DarkMode                           bool            `json:"dark_mode"`
	MedicalCertificateNotifications    bool            `json:"medical_certificate_notifications"`
	HideCategoryName                   bool            `json:"hide_category_name"`
	Phone                              *string         `json:"phone"`
	SubscriptionDurationEqualSportYear bool            `json:"subscription_duration_equal_sport_year"`
	BalanceSheetStartDay               int             `json:"balance_sheet_start_day"`
	BalanceSheetStartMonth             int             `json:"balance_sheet_start_month"`
	BalanceSheetYear                   int             `json:"balance_sheet_year"`
	SubscriptionDuration               int             `json:"subscription_duration"`
	MembershipDuration                 int             `json:"membership_duration"`
	MembershipStartingNumber           int             `json:"membership_starting_number"`
	DefaultMembershipType              *string         `json:"default_membership_type"`
	SubscriptionStartDay               int             `json:"subscription_start_day"`
	SubscriptionStartMonth             int             `json:"subscription_start_month"`
	CustomEndDate                      bool            `json:"custom_end_date"`
	SubscriptionEndDay                 int             `json:"subscription_end_day"`
	SubscriptionEndMonth               int             `json:"subscription_end_month"`
	LeadSportAssociationRole           *string         `json:"lead_sport_association_role"`
	LeadSportAssociationSize           *string         `json:"lead_sport_association_size"`
	LeadSportMarketChannel             *string         `json:"lead_sport_market_channel"`
	DefaultPaymentCategory             *string         `json:"default_payment_category"`
	DefaultPaymentCategoryCourses      *string         `json:"default_payment_category_courses"`
	CanImpersonate                     bool            `json:"can_impersonate"`
	RequiresWelcome                    bool            `json:"requires_welcome"`
	Onboarding                         *UserOnboarding `json:"onboarding"`
}

type UserOnboarding struct {
	ID                string `json:"users_onboarding_id"`
	UserID            string `json:"user"`
	Show              bool   `json:"show"`
	CreateMembership  bool   `json:"create_membership"`
	ViewMembership    bool   `json:"view_membership"`
	ApprovePayment    bool   `json:"approve_payment"`
	DownloadInvoice   bool   `json:"download_invoice"`
	ViewCollaborators bool   `json:"view_collaborators"`
	ViewSettings      bool   `json:"view_settings"`
}
