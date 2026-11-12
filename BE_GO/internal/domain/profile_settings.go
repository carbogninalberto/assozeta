package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

// ProfileSettings are the 30 User columns profile_settings reads and
// UserSettingsSerializer writes, in serializer field order.
type ProfileSettings struct {
	EnumerateInvoices                  bool    `db:"enumerate_invoices"`
	OnlinePayments                     bool    `db:"online_payments"`
	BalanceSheetYear                   int64   `db:"balance_sheet_year"`
	BalanceSheetStartDay               int64   `db:"balance_sheet_start_day"`
	BalanceSheetStartMonth             int64   `db:"balance_sheet_start_month"`
	TemporaryInvoiceDeletion           bool    `db:"temporary_invoice_deletion"`
	AutoArchive                        bool    `db:"auto_archive"`
	AutoMarkAttendance                 bool    `db:"auto_mark_attendance"`
	PaymentDateEqualInvoiceDate        bool    `db:"payment_date_equal_invoice_date"`
	StartingNumberInvoices             int64   `db:"starting_number_invoices"`
	AutoPaidPayment                    bool    `db:"auto_paid_payment"`
	FullInstallmentsPlan               bool    `db:"full_installments_plan"`
	ShowZeroPayments                   bool    `db:"show_zero_payments"`
	DarkMode                           bool    `db:"dark_mode"`
	MedicalCertificateNotifications    bool    `db:"medical_certificate_notifications"`
	HideCategoryName                   bool    `db:"hide_category_name"`
	SubscriptionDurationEqualSportYear bool    `db:"subscription_duration_equal_sport_year"`
	SubscriptionDuration               int64   `db:"subscription_duration"`
	MembershipDuration                 int64   `db:"membership_duration"`
	MembershipStartingNumber           int64   `db:"membership_starting_number"`
	DefaultMembershipType              *string `db:"default_membership_type"`
	SubscriptionStartDay               int64   `db:"subscription_start_day"`
	SubscriptionStartMonth             int64   `db:"subscription_start_month"`
	CustomEndDate                      bool    `db:"custom_end_date"`
	SubscriptionEndDay                 int64   `db:"subscription_end_day"`
	SubscriptionEndMonth               int64   `db:"subscription_end_month"`
	DisableAccountCreation             bool    `db:"disable_account_creation"`
	ForceAccountCreation               bool    `db:"force_account_creation"`
	DefaultPaymentCategory             *string `db:"default_payment_category_id"`
	DefaultPaymentCategoryCourses      *string `db:"default_payment_category_courses_id"`
}

// ProfileSettingsUser is the owner row: its settings, the table preferences
// GET renders and the names auditlog uses for the object representation.
type ProfileSettingsUser struct {
	ID        string          `db:"user_id"`
	FirstName string          `db:"first_name"`
	LastName  string          `db:"last_name"`
	Tables    json.RawMessage `db:"tables_settings"`
	ProfileSettings
}

// ProfileReview is the association's review link (profile_integrations).
type ProfileReview struct {
	URL     *string `db:"review_url"`
	Enabled bool    `db:"review_url_enabled"`
}

// ProfileSettingsAudit is one auditlog entry with its AuditLogIndex row.
type ProfileSettingsAudit struct {
	Model, Key, Representation string
	Action                     int
	Changes                    map[string][2]string
	AssociationID              string
	ResolutionPath             string
	Context                    LoginAuditContext
	At                         time.Time
}

var (
	// ErrProfileNotAssociation is User.sport_association for an athlete:
	// PermissionDenied('User is not a sport association').
	ErrProfileNotAssociation = errors.New("user is not a sport association")
	// ErrProfileCardMissing is profile_settings' unreachable 404 when the
	// configuration created at the start of the request is gone.
	ErrProfileCardMissing = errors.New("membership card configuration not found")
)

type ProfileSettingsStore interface {
	BeginProfileSettings(context.Context) (ProfileSettingsTransaction, error)
}

type ProfileSettingsTransaction interface {
	// OwnerAssociation is SportAssociation.objects.get(user=owner).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	User(ctx context.Context, owner string, lock bool) (ProfileSettingsUser, error)
	// PaymentCategory reports whether the category exists (default manager,
	// archived and deleted rows included) and its association.
	PaymentCategory(ctx context.Context, id string) (bool, *string, error)
	SaveSettings(ctx context.Context, owner string, values ProfileSettings) error
	FirstCard(ctx context.Context, association string) (*MembershipCardConfiguration, error)
	SaveCard(ctx context.Context, id string, emit bool, template json.RawMessage) error
	Review(ctx context.Context, association string, lock bool) (ProfileReview, error)
	SaveReview(ctx context.Context, association string, url *string, enabled bool) error
	AppendAudit(ctx context.Context, event ProfileSettingsAudit) error
	Commit() error
	Rollback() error
}
