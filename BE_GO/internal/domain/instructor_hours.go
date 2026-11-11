package domain

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// InstructorHour is one application_instructorhours row; JSON columns are
// their stored text, Date is ISO 'YYYY-MM-DD'.
type InstructorHour struct {
	ID                string    `db:"instructor_hours_id"`
	InstructorID      string    `db:"instructor_id"`
	CreationDate      time.Time `db:"creation_date"`
	Date              string    `db:"date"`
	CompensationType  string    `db:"compensation_type"`
	Hours             string    `db:"hours"`
	HourlyBilling     string    `db:"hourly_billing"`
	PercentageBilling *string   `db:"percentage_billing"`
	Courses           *string   `db:"courses"`
	Period            *string   `db:"period"`
	Amount            string    `db:"amount"`
	Paid              bool      `db:"paid"`
	PaymentID         *string   `db:"payment_id"`
	Notes             *string   `db:"notes"`
	CalculationData   *string   `db:"calculation_data"`
	DocumentID        *string   `db:"document_id"`
}

// InstructorHourListed is a listed hour with its document token and payment flag.
type InstructorHourListed struct {
	InstructorHour
	DocumentToken *string `db:"document_token"`
	PaymentPaid   *bool   `db:"payment_paid"`
}

// InstructorHoursQuery is instructor_hours_list's queryset: the scope user's
// instructor, an optional inclusive date range and an icontains search.
type InstructorHoursQuery struct {
	Owner, Instructor string
	From, To          *string
	Search            *string
}

// InstructorHoursPayment is the related Payment a serializer field resolved.
type InstructorHoursPayment struct {
	ID            string  `db:"payment_id"`
	AssociationID *string `db:"sport_association_id"`
}

// InstructorHoursCourse is one course of the calculation.
type InstructorHoursCourse struct {
	ID    string  `db:"course_id"`
	Title *string `db:"title"`
	Type  int     `db:"course_type"`
	Multi bool    `db:"multi_payments"`
}

// InstructorHoursSubscription is one course subscription of the calculation
// with its associate's names (nil without subscription or associate) and its
// single payment.
type InstructorHoursSubscription struct {
	ID           string     `db:"course_subscription_id"`
	HasAssociate bool       `db:"has_associate"`
	FirstName    *string    `db:"first_name"`
	LastName     *string    `db:"last_name"`
	PaymentID    *string    `db:"payment_id"`
	Paid         *bool      `db:"payment_paid"`
	Amount       *string    `db:"payment_amount"`
	PaymentDate  *time.Time `db:"payment_date"`
}

// InstructorHoursPaid is one paid payment of a membership or installment.
type InstructorHoursPaid struct {
	InstallmentID *string   `db:"course_subscription_installment_id"`
	Amount        string    `db:"amount"`
	PaymentDate   time.Time `db:"payment_date"`
}

// InstructorHoursAudit is one auditlog entry of an hour ("InstructorHours")
// or a document ("Document").
type InstructorHoursAudit struct {
	Model   string
	Key     string
	Action  int
	Changes map[string][2]string
	// Instructor, for an hour stored when its entry is written, indexes the
	// entry through the instructor's owner's association (else the actor's).
	Instructor *string
}

var (
	// ErrInstructorHoursNotPorted refuses list rows whose compensation
	// document would be printed (print_document_compensation).
	ErrInstructorHoursNotPorted = refusal.New("instructor hours compensation printing is not ported")
)

type InstructorHoursTransaction interface {
	// InstructorAssociation is the instructor (found) and its owner's
	// association (instructor_resolver), for the serializer's tenant scope.
	InstructorAssociation(ctx context.Context, id string) (found bool, association *string, err error)
	// OwnedHour is the hour of the user's instructor (nil when either is missing).
	OwnedHour(ctx context.Context, owner, instructor, id string) (*InstructorHour, error)
	CountHours(ctx context.Context, query InstructorHoursQuery) (int64, error)
	Hours(ctx context.Context, query InstructorHoursQuery, limit, offset int64) ([]InstructorHourListed, error)
	// VisiblePayment is Payment.objects (not deleted, group-aware) by pk.
	VisiblePayment(ctx context.Context, id string, group *string) (*InstructorHoursPayment, error)
	DocumentExists(ctx context.Context, id string) (bool, error)
	// FirstAssociation is SportAssociation.objects.filter(user=user).first().
	FirstAssociation(ctx context.Context, user string) (*string, error)
	InsertHour(ctx context.Context, row InstructorHour) error
	UpdateHour(ctx context.Context, row InstructorHour) error
	MarkPaid(ctx context.Context, id string) error
	// CreatedChanges / DeletedChanges render auditlog's diff from the stored row.
	CreatedChanges(ctx context.Context, id string) (map[string][2]string, error)
	DeletedChanges(ctx context.Context, id string) (map[string][2]string, error)
	DeleteHour(ctx context.Context, id string) error
	// DocumentDeletable reports whether Document.delete() would only null
	// SET_NULL references; otherwise DeleteDocumentCascade runs the collector.
	DocumentDeletable(ctx context.Context, id string) (bool, error)
	// DeleteDocumentCascade is Document.delete() through the shared deletion collector
	// (cascaded rows, links, SET_NULL updates, one audit entry per deleted row).
	DeleteDocumentCascade(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	// DeleteDocument nulls its SET_NULL references and deletes it, returning its delete diff.
	DeleteDocument(ctx context.Context, id string) (map[string][2]string, error)
	AppendAudit(ctx context.Context, event InstructorHoursAudit, association *string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type InstructorHoursStore interface {
	BeginInstructorHours(ctx context.Context) (InstructorHoursTransaction, error)
	// OwnedInstructorExists is Instructor.objects.filter(instructor_id=id, user=owner).exists().
	OwnedInstructorExists(ctx context.Context, id, owner string) (bool, error)
	// Courses are the live group-aware courses of the owner's associations.
	Courses(ctx context.Context, ids []string, owner string, group *string) ([]InstructorHoursCourse, error)
	CourseSubscriptions(ctx context.Context, course string) ([]InstructorHoursSubscription, error)
	MembershipPayments(ctx context.Context, subscription string, from, to time.Time, group *string) ([]InstructorHoursPaid, error)
	InstallmentPayments(ctx context.Context, subscription string, from, to time.Time, group *string) ([]InstructorHoursPaid, error)
}
