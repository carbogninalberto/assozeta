package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Rows the HTML views of BE/docmanager/views/printing_views.py read. JSON
// columns are their jsonb text (nil for SQL NULL).

// ViewAssociation is a SportAssociation with its owner's e-mail.
type ViewAssociation struct {
	ID, UserID           string
	UserRole             int
	UserEmail            *string
	Denomination         string
	AddressCity          *string
	Address              *string
	AddressCap           *string
	TaxCode              string
	Logo                 *string
	Regulation           *string
	Demand               *string
	AdditionalSections   *string
	AdditionalFields     *string
	DocumentHeader       *string
	InvoiceFooter        *string
	SubscriptionTemplate *string
	Federation           *string
	EnrollNumber         *string
	Iban                 *string
	Website              *string
	Abbreviated          *string
	VatNumber            *string
	Whatsapp             *string
	Sport                *string
	ShowRegulation       [3]bool // to members, both, athletes
	ShowDemand           [3]bool
}

// ViewAssociate is an Associate.
type ViewAssociate struct {
	ID                                      string
	FirstName, LastName                     *string
	Sex                                     string
	TaxCode                                 *string
	BornDate                                *time.Time
	BornCity, Address, AddressCity          *string
	AddressCap, Nationality, NationalityRes *string
	Email, Phone, Phone2, Phone3, Phone4    *string
	PhoneLabel, Phone2Label, Phone3Label    *string
	Phone4Label, Notes                      *string
}

// ViewSubscription is a Subscription with its payment's meta.
type ViewSubscription struct {
	ID, Association     string
	AssociateID         *string
	CreationDate        time.Time
	Type, Role          int64
	AdditionalFields    *string
	SignatureURL        *string
	SignatureStorageKey *string
	HasPayment          bool
	PaymentMeta         *string
}

// ViewPayment is a compensation payment.
type ViewPayment struct {
	ID           string
	Association  *string
	InstructorID *string
	SignatureID  *string
	Amount       string
	PaymentDate  *time.Time
	Notes        *string
}

// ViewInstructor is an Instructor.
type ViewInstructor struct {
	IsVolunteer                                      bool
	FirstName, LastName                              string
	TaxCode, Address, CivicNumber, AddressCity       *string
	BornDate                                         *time.Time
	BornCity, BornProvince, Phone, Email, StudyTitle *string
	Role                                             *string
	StipulatedContractIn                             *time.Time
}

// ViewHours is an InstructorHours row's period data.
type ViewHours struct {
	CompensationType string
	Date             time.Time
	Period           *string
}

// ViewTemplate is a SportAssociationModuleTemplates row.
type ViewTemplate struct {
	Association                        string
	Template                           *string
	Footer, Header, CustomFooterHeader bool
	CustomFooter, CustomHeader         *string
}

// ViewAppointment is a MedicalAppointments row with its subscription.
type ViewAppointment struct {
	Region      string
	Date        *time.Time
	Sport       *string
	Meta        *string
	Association string
	AssociateID *string
}

var (
	// ErrViewNotFound is DoesNotExist (or .first() returning None).
	ErrViewNotFound = errors.New("document view: row not found")
	// ErrViewTutorWrite: get_main_tutor() would promote a tutor relation (a write).
	ErrViewTutorWrite = refusal.New("document view: main tutor promotion")
	// ErrViewCourseTitle: a course without a title (", ".join raises TypeError).
	ErrViewCourseTitle = errors.New("document view: course without title")
)

// DocumentViewStore reads the rows the HTML views render.
type DocumentViewStore interface {
	Association(ctx context.Context, id string) (*ViewAssociation, error)
	// ActiveAssociation is scoped_queryset(SportAssociation.objects).get(pk=id) (live rows).
	ActiveAssociation(ctx context.Context, id string, scope *string) (*ViewAssociation, error)
	// AssociationsOfUser is SportAssociation.objects.filter(user=user)[:21].
	AssociationsOfUser(ctx context.Context, user string) ([]ViewAssociation, error)
	Associate(ctx context.Context, id string) (*ViewAssociate, error)
	// MainTutor is Associate.get_main_tutor(): nil, the tutor, or ErrViewTutorWrite.
	MainTutor(ctx context.Context, associate string) (*ViewAssociate, error)
	Subscription(ctx context.Context, id string, scope RenderScope) (*ViewSubscription, error)
	// CoursesList is Subscription.get_courses_list().
	CoursesList(ctx context.Context, subscription string) (string, error)
	Compensation(ctx context.Context, id string, scope RenderScope) (*ViewPayment, error)
	Instructor(ctx context.Context, id string) (*ViewInstructor, error)
	Signature(ctx context.Context, id string) (string, error)
	Hours(ctx context.Context, payment string) ([]ViewHours, error)
	Template(ctx context.Context, id string) (*ViewTemplate, error)
	Appointment(ctx context.Context, id string) (*ViewAppointment, error)
}
