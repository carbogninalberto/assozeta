package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrSubscriptionCreateServer is an exception Django raises (the 500 page).
	ErrSubscriptionCreateServer = errors.New("subscription create server error")
	// ErrSubscriptionCreateNotPorted refuses input this port does not handle (the
	// 500 page, nothing committed).
	ErrSubscriptionCreateNotPorted = errors.New("subscription create input not ported")
	// ErrSubscriptionCreateDuplicate is DuplicateSubscriptionError (409).
	ErrSubscriptionCreateDuplicate = errors.New("subscription already exists")
)

// SubscriptionCreateInvalid is a 400 whose body is rendered by the handler:
// Fields (serializer.errors codes) or Detail (a ValidationError dict, as is).
type SubscriptionCreateInvalid struct {
	Fields map[string][]string
	Detail map[string]string
}

func (e *SubscriptionCreateInvalid) Error() string { return "invalid subscription data" }

// SubscriptionCreateSettings are the association and its owner's settings
// create_subscription reads (the owner is request.user for owners and
// collaborators, so both settings sources are the same row).
type SubscriptionCreateSettings struct {
	AssociationID            string  `db:"sport_association_id"`
	OwnerID                  string  `db:"user_id"`
	QuotesEnabled            bool    `db:"enable_quotes_management"`
	SubscriptionFee          string  `db:"subscription_fee"`
	MembershipFee            *string `db:"membership_fee"`
	MultipleSubscriptionFee  bool    `db:"multiple_subscription_fee"`
	MultipleMembershipFee    bool    `db:"multiple_membership_fee"`
	SubscriptionPlans        []byte  `db:"subscription_fee_plans"`
	MembershipPlans          []byte  `db:"membership_fee_plans"`
	StartDay                 int     `db:"subscription_start_day"`
	StartMonth               int     `db:"subscription_start_month"`
	CustomEnd                bool    `db:"custom_end_date"`
	EndDay                   int     `db:"subscription_end_day"`
	EndMonth                 int     `db:"subscription_end_month"`
	SubscriptionDuration     int     `db:"subscription_duration"`
	MembershipDuration       int     `db:"membership_duration"`
	MembershipStartingNumber int64   `db:"membership_starting_number"`
	DefaultMembershipType    *string `db:"default_membership_type"`
	ShowZeroPayments         bool    `db:"show_zero_payments"`
	DefaultPaymentCategory   *string `db:"default_payment_category_id"`
	// DefaultCoursesCategory is default_payment_category_courses (the quick re-subscription).
	DefaultCoursesCategory *string `db:"default_payment_category_courses_id"`
	Denomination           *string `db:"denomination"`
	OwnerEmail             string  `db:"owner_email"`
}

// SubscriptionCreateMember is a user create_subscription continues with
// (request.user, or the new member's account).
type SubscriptionCreateMember struct {
	ID         string `db:"user_id"`
	Role       int    `db:"role"`
	StartDay   int    `db:"subscription_start_day"`
	StartMonth int    `db:"subscription_start_month"`
	CustomEnd  bool   `db:"custom_end_date"`
	EndDay     int    `db:"subscription_end_day"`
	EndMonth   int    `db:"subscription_end_month"`
}

// SubscriptionCreateNewUser is UserSerializer.save()'s account (create_user,
// then the generated username and set_password, saved again).
type SubscriptionCreateNewUser struct {
	ID, TokenUsername, Username, Email, PasswordHash, FirstName, LastName string
	Phone                                                                 *string
	Role                                                                  int
	Joined                                                                time.Time
}

// SubscriptionQuickCertificate is the certificate of one of an associate's subscriptions.
type SubscriptionQuickCertificate struct {
	ID         string     `db:"medical_id"`
	Expiration *time.Time `db:"expiration_date"`
}

// SubscriptionCreateExisting is the first subscription subscription_exists finds.
type SubscriptionCreateExisting struct {
	ID          string     `db:"subscription_id"`
	Start       *time.Time `db:"start_date"`
	End         *time.Time `db:"end_date"`
	AssociateID *string    `db:"associate_id"`
	TaxCode     *string    `db:"tax_code"`
}

// SubscriptionCreatePayment is a payment row Payment.objects.create inserts.
type SubscriptionCreatePayment struct {
	ID, AssociationID, AssociateID, UserID string
	Amount                                 string
	CategoryID, AccountID                  *string
	MetaCategories, Meta                   *string // JSON text or nil
	PaymentDate, Created                   time.Time
}

// SubscriptionCreateRow is the Subscription row Subscription.objects.create inserts.
type SubscriptionCreateRow struct {
	ID, AssociationID, AssociateID, UserID string
	Type, Role, Status                     int64
	MedicalID, PaymentID                   *string
	CustomData, AdditionalFields           *string
	Meta                                   string
	Number                                 string
	SubscriptionType                       *string
	Created                                time.Time
	Start, End                             *CalendarDate
}

// SubscriptionCreateMembership is a SubscriptionMembership row.
type SubscriptionCreateMembership struct {
	ID, SubscriptionID, AssociationID, AssociateID string
	Number                                         string
	Type                                           string
	Price                                          *string
	PaymentID                                      *string
	Created                                        time.Time
	Start, End                                     *CalendarDate
}

// SubscriptionCreateAudit is one auditlog entry. Created entries render the
// stored row (Table/Column) with Overrides; others use Changes.
type SubscriptionCreateAudit struct {
	App, Model, Representation, Table, Column, Key string
	Action                                         int
	Created                                        bool
	Overrides, Changes                             map[string][2]string
	Association                                    *string
	Path                                           string
	Context                                        LoginAuditContext
	At                                             time.Time
}

type SubscriptionCreateTransaction interface {
	SubscriptionEditTransaction
	SubscriptionRenewAssociateStore
	// Settings is user.sport_association (exactly one live association, else
	// nil) with its owner's settings, the association row locked.
	Settings(ctx context.Context, user string) (*SubscriptionCreateSettings, int, error)
	Existing(ctx context.Context, association string, first, last, tax *string, start, end CalendarDate) (*SubscriptionCreateExisting, error)
	FutureRenewal(ctx context.Context, association string, end CalendarDate, tax *string) (bool, error)
	Certificate(ctx context.Context, id string) (found bool, user *string, err error)
	// LinkableCertificate is _linkable_medical_certificates: the certificate by id when it
	// belongs to one of accounts, is the certificate of a subscription of the association, or
	// has no account and no subscription of another association (locked).
	LinkableCertificate(ctx context.Context, id, association string, accounts []string) (found bool, user *string, err error)
	SaveCertificateUser(ctx context.Context, id, user string) error
	// CertificateExpiration is the stored expiration_date of the certificate.
	CertificateExpiration(ctx context.Context, id string) (*time.Time, error)
	// SaveCertificate is MedicalCertificate.save() of the loaded certificate
	// with its user and, when given, a new expiration date.
	SaveCertificate(ctx context.Context, id, user string, expiration *time.Time) error
	Category(ctx context.Context, id *string, association string) (*string, error)
	DefaultBankAccount(ctx context.Context, association string) (*string, error)
	ReusablePayments(ctx context.Context, associate, association, amount string, day time.Time) ([]string, bool, error)
	PaymentAmount(ctx context.Context, id string) (string, error)
	InsertPayment(ctx context.Context, row SubscriptionCreatePayment) error
	LatestNumber(ctx context.Context, association string) (*string, error)
	InsertSubscription(ctx context.Context, row SubscriptionCreateRow) error
	InsertMembership(ctx context.Context, row SubscriptionCreateMembership) error
	InsertRelation(ctx context.Context, associate, tutor string) (int64, error)
	AuditEntry(ctx context.Context, entry SubscriptionCreateAudit) error
	// QuickAssociate is Associate.objects.get(associate_id=id): a live row (nil when none).
	QuickAssociate(ctx context.Context, id string) (*SubscriptionEditAssociate, error)
	// InsertFamily is Family.objects.create(type=kind) with its create entry.
	InsertFamily(ctx context.Context, id, kind string, audit LoginAuditContext, at time.Time) error
	// RenderPayment is what PaymentSerializer reads for a stored payment.
	RenderPayment(ctx context.Context, id string) (PaymentStateRender, error)
	// QuickAssociationExists is SportAssociation.objects.get(sport_association_id=id) finding a row.
	QuickAssociationExists(ctx context.Context, id string) (bool, error)
	// QuickCertificates are the certificates of Subscription.objects.filter(associate=...), one per subscription.
	QuickCertificates(ctx context.Context, associate string) ([]SubscriptionQuickCertificate, error)
	// InsertSignature is Signature.objects.create(signature=data, user=user) with its create entry.
	InsertSignature(ctx context.Context, id, user, data string, audit LoginAuditContext, at time.Time) error
	// SaveSubscriptionSignature is save(update_fields=['signature_url', 'signature_storage_key']) of the new subscription.
	SaveSubscriptionSignature(ctx context.Context, id, association string, url, key *string, audit LoginAuditContext, at time.Time) error
	// MemberByUsername is User.objects.get(username=...) (live users).
	MemberByUsername(ctx context.Context, username string) (*SubscriptionCreateMember, error)
	// MemberByEmail is User.objects.filter(email=email).first() (live users, by key).
	MemberByEmail(ctx context.Context, email string) (*SubscriptionCreateMember, error)
	// Member is the user's role and season settings.
	Member(ctx context.Context, id string) (*SubscriptionCreateMember, error)
	// InsertNewUser writes the account with its create entry (TokenUsername) and the username update entry.
	InsertNewUser(ctx context.Context, user SubscriptionCreateNewUser, audit LoginAuditContext) error
}

type SubscriptionCreateStore interface {
	BeginSubscriptionCreate(ctx context.Context) (SubscriptionCreateTransaction, error)
}

// SubscriptionNotificationStore persists NotificationManager's broadcast rows.
type SubscriptionNotificationStore interface {
	// ValidateBroadcast is validate_broadcast's rows: the broadcast (created
	// when missing) and the user's subscription; subscribed reports a new one.
	ValidateBroadcast(ctx context.Context, user, name string, audit LoginAuditContext, at time.Time) (subscribed bool, err error)
}
