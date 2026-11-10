package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrSubscriptionReadsServer is an exception Django does not handle (the 500 page).
	ErrSubscriptionReadsServer = errors.New("subscription reads unhandled error")
	// ErrSubscriptionReadsNotPorted refuses inputs whose Django behaviour is
	// not ported (the 500 page, never a guessed body).
	ErrSubscriptionReadsNotPorted = errors.New("subscription reads input not ported")
	// ErrSubscriptionReadsAthlete is subscription_list_archived's Response(status=401) for athletes.
	ErrSubscriptionReadsAthlete = errors.New("subscription reads athlete")
	// ErrSubscriptionReadsNotFound is subscription_info's {'msg': 'Subscription not found.'} (404).
	ErrSubscriptionReadsNotFound = errors.New("subscription reads not found")
	// ErrSubscriptionReadsNotAllowed is subscription_list_all's {'msg': 'not allowed'} (403).
	ErrSubscriptionReadsNotAllowed = errors.New("subscription reads not allowed")
)

// SubscriptionReadsOrder is one ORDER BY term (an SQL expression the repository resolves).
type SubscriptionReadsOrder struct {
	// Column is a Subscription column, or "associate.first_name", "associate.last_name", "age".
	Column     string
	Descending bool
}

// SubscriptionReadsFilter is the subscription queryset one of the views builds.
type SubscriptionReadsFilter struct {
	// AssociationID is sport_association=...; OwnerUser is sport_association__user=... (the field mode).
	AssociationID *string
	OwnerUser     *string
	// UserID and AssociationKey are the athlete branches' user= and sport_association__sport_association_id=.
	UserID         *string
	AssociationKey *string
	// AssociationNull is sport_association__sport_association_id=None (no rows).
	AssociationNull bool
	Archived        *bool
	// Group is the X-Group-Id of the group-aware manager; Scope scoped_queryset's session association.
	Group *string
	Scope *string
	// Empty is a lookup Django compiles to EmptyResultSet (no rows).
	Empty        bool
	TagsAny      []string
	TagsAll      []string
	BornAtMost   *time.Time
	BornAfter    *time.Time
	ExcludeTypes []int
	Status       *int64
	StatusIn     []int64
	StartAfter   *time.Time
	EndBefore    *time.Time
	// Current is start_date <= day AND end_date >= day; Overlap is the period filter.
	Current          *time.Time
	OverlapStart     *time.Time
	OverlapEnd       *time.Time
	EndContains      *string
	UserContains     *string
	Smart            *PersonaSearch
	CustomContains   *string
	MedicalMissing   bool
	MedicalExpiredBy *time.Time
	PaymentUnpaid    bool
	Types            []int
	// ExcludeCourse is exclude(subscription_id__in=<the course's live course subscriptions>).
	ExcludeCourse *string
	Order         []SubscriptionReadsOrder
}

// SubscriptionReadsRow is one subscription with what the list serializers read.
type SubscriptionReadsRow struct {
	ID                  string     `db:"subscription_id"`
	AssociationID       string     `db:"sport_association_id"`
	AssociateID         *string    `db:"associate_id"`
	SupplierID          *string    `db:"supplier_id"`
	InstructorID        *string    `db:"instructor_id"`
	CustomData          *string    `db:"custom_data"`
	AdditionalFields    *string    `db:"additional_fields"`
	UserID              string     `db:"user_id"`
	Type                int        `db:"type"`
	Role                int        `db:"role"`
	AcceptanceDate      *time.Time `db:"acceptance_date"`
	ResignationDate     *time.Time `db:"resignation_date"`
	MedicalID           *string    `db:"medical_id"`
	StatusFlag          int        `db:"status_flag"`
	CreationDate        time.Time  `db:"creation_date"`
	StartDate           *time.Time `db:"start_date"`
	EndDate             *time.Time `db:"end_date"`
	SignatureURL        *string    `db:"signature_url"`
	SignatureStorageKey *string    `db:"signature_storage_key"`
	DocumentID          *string    `db:"document_pdf_id"`
	PaymentID           *string    `db:"payment_id"`
	Archived            bool       `db:"archived"`
	Draft               bool       `db:"draft"`
	Trial               bool       `db:"trial"`
	Meta                *string    `db:"meta"`
	Number              *string    `db:"subscription_number"`
	SubscriptionType    *string    `db:"subscription_type"`
	GroupID             *string    `db:"group_id"`
	Deleted             bool       `db:"deleted"`
	Competitive         bool       `db:"competitive"`
	Notes               *string    `db:"notes"`
	// The forward relations the serializers read.
	UserUsername       string     `db:"user_username"`
	UserEmail          *string    `db:"user_email"`
	UserFirstName      *string    `db:"user_first_name"`
	UserLastName       *string    `db:"user_last_name"`
	UserPhone          *string    `db:"user_phone"`
	UserRole           int        `db:"user_role"`
	MedicalExpiration  *time.Time `db:"medical_expiration_date"`
	MedicalCompetitive *bool      `db:"medical_competitive"`
	MedicalDocumentID  *string    `db:"medical_document_id"`
	MedicalToken       *string    `db:"medical_token"`
	DocumentToken      *string    `db:"document_token"`
	PaymentAmount      *string    `db:"payment_amount"`
	PaymentPaid        *bool      `db:"payment_paid"`
	PaymentDate        *time.Time `db:"payment_payment_date"`
	// Tags is the tags M2M as a JSON array of {tag_id, tag_name}.
	Tags string `db:"tags"`
	// FutureRenewal is the future_renewal_exists() annotation.
	FutureRenewal bool `db:"future_renewal"`
	Associate     *PersonaAssociate
}

// SubscriptionReadsPayments are the sums of an associate's live payments.
type SubscriptionReadsPayments struct {
	Count  int64  `db:"count"`
	Unpaid int64  `db:"unpaid"`
	Total  string `db:"total"`
	ToPay  string `db:"to_pay"`
}

// SubscriptionReadsCourse is one live course subscription of subscription_info with its course.
type SubscriptionReadsCourse struct {
	CourseSubscriptionRow
	Course CourseOverviewRow
}

// SubscriptionReadsInstallment is one installment of the info view's multi-payment list.
type SubscriptionReadsInstallment struct {
	ID                 string    `db:"course_subscription_installment_id"`
	CourseSubscription string    `db:"course_subscription_id"`
	PaymentID          *string   `db:"payment_id"`
	Amount             string    `db:"amount"`
	PaymentDate        time.Time `db:"payment_date"`
	Paid               bool      `db:"paid"`
	PaymentPaid        *bool     `db:"payment_paid"`
}

// SubscriptionReadsFile is one subscription file with its document.
type SubscriptionReadsFile struct {
	ID           string    `db:"subscription_file_id"`
	DocumentID   string    `db:"document_id"`
	Filename     string    `db:"filename"`
	Token        string    `db:"token"`
	CreationDate time.Time `db:"creation_date"`
}

// SubscriptionReadsCard is the association's first membership card configuration.
type SubscriptionReadsCard struct {
	EmitOnlyOnApproval bool    `db:"emit_only_on_approval"`
	CustomizedTemplate *string `db:"customized_template"`
}

// SubscriptionReadsRegistry is one attendance registry with its course title.
type SubscriptionReadsRegistry struct {
	Events      *string `db:"events"`
	CourseTitle *string `db:"course_title"`
}

// SubscriptionReadsStore is the data access of the five views.
type SubscriptionReadsStore interface {
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).get(user=...) (nil: DoesNotExist).
	ScopedAssociation(ctx context.Context, user string, scope *string) (*string, error)
	// LiveAssociation is SportAssociation.objects.filter(pk=...).first().
	LiveAssociation(ctx context.Context, id string) (bool, error)
	CountSubscriptions(ctx context.Context, filter SubscriptionReadsFilter) (int64, error)
	Subscriptions(ctx context.Context, filter SubscriptionReadsFilter, limit, offset int64) ([]string, error)
	Render(ctx context.Context, ids []string, group *string) (map[string]SubscriptionReadsRow, error)
	// Payments sums the live, group-aware unarchived payments of an associate (nil: associate IS NULL).
	Payments(ctx context.Context, associate *string, group *string) (SubscriptionReadsPayments, error)
	// NullTaxRenewal is has_future_renewal()'s lookup with associate__tax_code__iexact=None.
	NullTaxRenewal(ctx context.Context, association string, end time.Time, group *string) (bool, error)
	FieldValues(ctx context.Context, filter SubscriptionReadsFilter, column string, timestamp bool) ([]*string, error)
	// ExportTags is prefetch_related('tags') of current_view_subscriptions for the subscriptions, in the database's order.
	ExportTags(ctx context.Context, ids []string) ([]SubscriptionReadsExportTag, error)
	// AssociationDenomination is sport_association.denomination.
	AssociationDenomination(ctx context.Context, association string) (*string, error)
	// AllowedForAthlete are subscription_info's athlete subscription ids (own and same tax code).
	AllowedForAthlete(ctx context.Context, user string, group, scope *string) ([]string, error)
	// Subscription is the group-aware, scoped subscription by id (nil: none).
	Subscription(ctx context.Context, id string, group, scope *string, user *string, association *string) (*SubscriptionReadsRow, error)
	Courses(ctx context.Context, subscription string, scope *string) ([]SubscriptionReadsCourse, error)
	Installments(ctx context.Context, courseSubscriptions []string, group, scope *string) ([]SubscriptionReadsInstallment, error)
	Files(ctx context.Context, subscription string, scope *string) ([]SubscriptionReadsFile, error)
	Card(ctx context.Context, association string) (*SubscriptionReadsCard, error)
	Persona(ctx context.Context, associate string) (*PersonaListed, error)
	Registries(ctx context.Context, subscription string, scope *string) ([]SubscriptionReadsRegistry, error)
	// BalanceStart is the user's balance_sheet_start_day and balance_sheet_start_month.
	BalanceStart(ctx context.Context, user string) (day, month int, err error)
	Relations(ctx context.Context, associates []string) ([]PersonaRelation, error)
	Tutors(ctx context.Context, associates []string, group *string) ([]PersonaRelation, error)
	FamilyMembers(ctx context.Context, family, self string, group *string) ([]string, error)
}

// SubscriptionReadsExportTag is one prefetched tag of a subscription.
type SubscriptionReadsExportTag struct {
	SubscriptionID string `db:"subscription_id"`
	Name           string `db:"tag_name"`
}
