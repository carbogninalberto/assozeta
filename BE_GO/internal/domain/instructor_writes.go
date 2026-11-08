package domain

import (
	"context"
	"errors"
	"time"
)

// ErrInstructorWritesNotPorted refuses, before any write, input whose Django
// outcome Go does not reproduce.
var ErrInstructorWritesNotPorted = errors.New("instructor writes input not ported")

// InstructorRecord is one application_instructor row; dates are ISO
// 'YYYY-MM-DD', decimals their stored text.
type InstructorRecord struct {
	ID                       string  `db:"instructor_id"`
	UserID                   string  `db:"user_id"`
	FirstName                string  `db:"first_name"`
	LastName                 string  `db:"last_name"`
	Email                    *string `db:"email"`
	BornDate                 *string `db:"born_date"`
	BornCity                 *string `db:"born_city"`
	BornProvince             *string `db:"born_province"`
	TaxCode                  *string `db:"tax_code"`
	AddressCity              *string `db:"address_city"`
	Address                  *string `db:"address"`
	AddressProvince          *string `db:"address_province"`
	CivicNumber              *string `db:"civic_number"`
	StudyTitle               *string `db:"study_title"`
	Role                     *string `db:"role"`
	Phone                    *string `db:"phone"`
	Phone2                   *string `db:"phone_2"`
	Phone3                   *string `db:"phone_3"`
	Phone4                   *string `db:"phone_4"`
	Draft                    bool    `db:"draft"`
	AssociatedUserID         *string `db:"associated_user_id"`
	IsVolunteer              bool    `db:"is_volunteer"`
	StipulatedContractIn     *string `db:"stipulated_contract_in"`
	DefaultHourlyBilling     string  `db:"default_hourly_billing"`
	DefaultPercentageBilling *string `db:"default_percentage_billing"`
}

// InstructorWritesAudit is one auditlog entry with its index (nil: none).
type InstructorWritesAudit struct {
	Model, Key    string
	Action        int
	Changes       map[string][2]string
	AssociationID *string
	Path          string
}

// InstructorCompensationPayment is the Payment the compensation creates.
type InstructorCompensationPayment struct {
	ID, Amount, Description, UserID, InstructorID, AssociationID string
	CreationDate                                                 time.Time
	PaymentDate                                                  time.Time
	CategoryID, AccountID, GroupID                               *string
}

// InstructorLessonRow is an instructor as lessons-hours renders it.
type InstructorLessonRow struct {
	ID        string `db:"instructor_id"`
	UserID    string `db:"user_id"`
	FirstName string `db:"first_name"`
	LastName  string `db:"last_name"`
}

// InstructorLessonRegistry is a published attendance registry with its course.
type InstructorLessonRegistry struct {
	CourseID    string  `db:"course_id"`
	CourseTitle string  `db:"title"`
	Events      *string `db:"events"`
}

type InstructorWritesStore interface {
	BeginInstructorWrites(ctx context.Context) (InstructorWritesTransaction, error)
	// FirstAssociation is SportAssociation.objects.filter(user=user).first().
	FirstAssociation(ctx context.Context, user string) (*string, error)
	// Associations are SportAssociation.objects.filter(user=user) (up to two keys, for .get()).
	Associations(ctx context.Context, user string) ([]string, error)
	LessonInstructor(ctx context.Context, id string) (*InstructorLessonRow, error)
	// RequestingInstructor is Instructor.objects.filter(associated_user_id=acting[, user=owner]).first().
	RequestingInstructor(ctx context.Context, acting string, owner *string) (*InstructorLessonRow, error)
	// AssociationInstructors are the non-draft instructors of the association's owners, unordered.
	AssociationInstructors(ctx context.Context, association string) ([]InstructorLessonRow, error)
	PublishedRegistries(ctx context.Context, association string) ([]InstructorLessonRegistry, error)
}

type InstructorWritesTransaction interface {
	Commit() error
	Rollback() error
	FirstAssociation(ctx context.Context, user string) (*string, error)
	Associations(ctx context.Context, user string) ([]string, error)
	// OwnedInstructor is Instructor.objects.filter(instructor_id=id, user=owner).first(), locked.
	OwnedInstructor(ctx context.Context, id, owner string) (*InstructorRecord, error)
	InstructorIDTaken(ctx context.Context, id string, exclude *string) (bool, error)
	DocumentExists(ctx context.Context, id string) (bool, error)
	InsertInstructor(ctx context.Context, row InstructorRecord) error
	UpdateInstructor(ctx context.Context, row InstructorRecord) error
	SetDocuments(ctx context.Context, instructor string, documents []string) error
	CreatedChanges(ctx context.Context, model, id string) (map[string][2]string, error)
	AppendAudit(ctx context.Context, event InstructorWritesAudit, audit LoginAuditContext, at time.Time) error
	// DeleteInstructor runs the collector and returns the delete entries in
	// signal order without their index (the service resolves it).
	DeleteInstructor(ctx context.Context, id string) ([]InstructorWritesAudit, error)
	LiveAssociation(ctx context.Context, id string) (bool, error)
	// SelectableHours are the instructor's unpaid hours without payment among ids, in heap order.
	SelectableHours(ctx context.Context, instructor string, ids []string) ([]InstructorHour, error)
	CompensationCategory(ctx context.Context) (*string, error)
	DefaultBankAccount(ctx context.Context, association string) (*string, error)
	GroupExists(ctx context.Context, id string) (bool, error)
	InsertPayment(ctx context.Context, payment InstructorCompensationPayment) error
	LinkHour(ctx context.Context, hour, payment string) error
	SetPaymentNotes(ctx context.Context, payment, notes string) error
}
