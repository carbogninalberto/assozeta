package domain

import (
	"context"
	"errors"
	"time"
)

// RenderScope narrows a lookup like scoped_queryset plus the group-aware
// manager: Association is the impersonation boundary (nil outside one),
// Effective/Athlete the scoped person, Group the X-Group-Id filter.
type RenderScope struct {
	Association *string
	Effective   string
	Athlete     bool
	Group       *string
}

// RenderSubscription is the subscription a PDF names.
type RenderSubscription struct {
	ID, Association string
	Denomination    *string
	HasAssociate    bool
	First, Last     *string
	Document        *string
}

// RenderInvoice is an invoice with the association it belongs to.
type RenderInvoice struct {
	ID, Association string
	Denomination    *string
	Document        *string
}

// RenderPayment is the invoice's payment (or a compensation payment).
type RenderPayment struct {
	ID, Association     string
	Denomination        *string
	Amount              string
	Date                *time.Time
	HasAssociate        bool
	AssociateFirst      *string
	AssociateLast       *string
	AssociateEmail      *string
	HasInstructor       bool
	InstructorFirst     string
	InstructorLast      string
	InstructorOwnerUser string
}

// RenderCustomerInvoice is a customer (electronic) invoice.
type RenderCustomerInvoice struct {
	ID, Association string
	Denomination    *string
	Prefix          *string
	Number, Year    *int64
	Document        *string
}

// RenderTemplate is a module template of an association.
type RenderTemplate struct {
	ID, Association string
	Denomination    *string
	Name            string
}

// RenderTokenRecord names the rows HasDocumentToken reads for a route: the
// table, the column the uid is matched on and the column holding the document.
type RenderTokenRecord struct{ Table, Key, Document string }

// RenderAppointment is a medical appointment with its subscription's association.
type RenderAppointment struct {
	ID, Association string
	Subscription    string
	Denomination    *string
	Region          string
	Date            *string
	Document        *string
}

// RenderAppointmentForm is what the regional PDF form of an appointment reads:
// Text holds the stored columns by form key (nil for NULL), Address the
// associate's address and Created the appointment's creation date. Associate
// is nil for a subscription without one.
type RenderAppointmentForm struct {
	Associate *string
	Text      map[string]*string
	Address   *string
	Created   time.Time
}

// RenderFormTutor is the phone and e-mail of Associate.main_tutor.
type RenderFormTutor struct{ Phone, Email *string }

// RenderHours is an InstructorHours row linked to a compensation.
type RenderHours struct {
	ID, Instructor string
	Document       *string
	OwnerAssoc     *string
}

// NewRenderDocument is the Document.objects.create(filename=...) row.
type NewRenderDocument struct {
	ID, Token, Filename string
	Created             time.Time
}

// RenderLink is a row whose document foreign key becomes the new Document.
type RenderLink struct {
	Table, KeyColumn, Column, Field, Model, Representation string
	ID                                                     string
	Old                                                    *string
	IndexAssociation                                       *string
	IndexPath                                              string
}

var (
	ErrRenderNotFound = errors.New("document render: row not found")
	// ErrRenderMultiple is MultipleObjectsReturned; Count is the row count.
	ErrRenderMultiple = errors.New("document render: more than one row")
)

// DocumentRenderStore reads the rows the PDF views name and writes their effects.
type DocumentRenderStore interface {
	MemberDocumentStore
	// RecordDocumentToken is HasDocumentToken: token is the token of a document of the record the route's uid names.
	RecordDocumentToken(ctx context.Context, record RenderTokenRecord, uid, token string) (bool, error)
	ActorAssociation(ctx context.Context, user string) (*string, error)
	OwnedAssociations(ctx context.Context, user string) ([]string, error)
	Subscription(ctx context.Context, id string, scope RenderScope) (*RenderSubscription, error)
	Invoice(ctx context.Context, id string, scope RenderScope) (*RenderInvoice, error)
	InvoicePayments(ctx context.Context, invoice string, scope RenderScope) ([]RenderPayment, error)
	Compensation(ctx context.Context, id string, scope RenderScope) (*RenderPayment, error)
	CompensationHours(ctx context.Context, payment string) ([]RenderHours, error)
	CustomerInvoice(ctx context.Context, id string) (*RenderCustomerInvoice, error)
	Template(ctx context.Context, id, association string) (*RenderTemplate, error)
	FolderExists(ctx context.Context, id int64) (bool, error)
	Appointment(ctx context.Context, id string) (*RenderAppointment, error)
	// AppointmentForm reads the values of the appointment's regional PDF form.
	AppointmentForm(ctx context.Context, id string) (*RenderAppointmentForm, error)
	// AppointmentFormTutor is Associate.get_main_tutor(): nil, the tutor, or ErrViewTutorWrite.
	AppointmentFormTutor(ctx context.Context, associate string) (*RenderFormTutor, error)
	CreateDocument(ctx context.Context, document NewRenderDocument, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	CreateArchive(ctx context.Context, id, association, document string, folder int64, created time.Time, audit LoginAuditContext, at time.Time) error
	CreateSubscriptionFile(ctx context.Context, id string, subscription RenderSubscription, document string, created time.Time,
		audit LoginAuditContext, at time.Time) error
	Link(ctx context.Context, link RenderLink, document string, audit LoginAuditContext, at time.Time) error
	// SubscriptionSaveSeason reads what Subscription.save() computes before it writes.
	SubscriptionSaveSeason(ctx context.Context, subscription string) (SubscriptionSaveSeason, error)
}
