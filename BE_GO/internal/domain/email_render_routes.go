package domain

import (
	"context"
	"errors"
	"time"
)

// ErrEmailRenderNotFound: payment_request / invoice_send found no row (or a
// payment without association); Django raises AttributeError on None (500).
var ErrEmailRenderNotFound = errors.New("email render row not found")

// EmailRenderInstanceConfig is InstanceConfiguration.get_config() as the
// instance_email_brand tag reads it (Found false: no row).
type EmailRenderInstanceConfig struct {
	Found        bool      `db:"found"`
	SelfHosted   bool      `db:"self_hosted"`
	PrimaryColor string    `db:"primary_color"`
	Name         string    `db:"name"`
	LogoPath     string    `db:"logo_path"`
	UpdatedAt    time.Time `db:"updated_at"`
}

// EmailRenderAssociation is request.user's association with what the
// e-mails read from it: its denomination and its owner's email and
// online_payments flag.
type EmailRenderAssociation struct {
	ID             string  `db:"sport_association_id"`
	Denomination   *string `db:"denomination"`
	OwnerEmail     *string `db:"owner_email"`
	OnlinePayments *bool   `db:"online_payments"`
}

// CollaborationInvite is the CollaborationInvites row collaborators_add creates.
type CollaborationInvite struct {
	ID, User, Email, Token string
	Role                   int64
	Permissions            *string // json.dumps of the value; nil is NULL
	Created, Expiration    time.Time
}

// CollaborationInviteAudit is the auditlog create entry of the invitation.
type CollaborationInviteAudit struct {
	Context                       LoginAuditContext
	AssociationID, ResolutionPath *string
	At                            time.Time
}

type CollaboratorInviteTransaction interface {
	CreateInvite(ctx context.Context, invite CollaborationInvite) error
	AppendInviteAudit(ctx context.Context, id string, event CollaborationInviteAudit) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	// LiveAssociations returns the live associations of user (SportAssociation.objects.filter(user=...)).
	LiveAssociations(ctx context.Context, user string, impersonation *string) ([]EmailRenderAssociation, error)
	Commit() error
	Rollback() error
}

// EmailPaymentRequest is the payment payment_request reads (scoped, live,
// group-aware), with its associate, association and that association's owner.
type EmailPaymentRequest struct {
	ID             string  `db:"payment_id"`
	Paid           bool    `db:"paid"`
	Subject        int64   `db:"subject"`
	Amount         string  `db:"amount"`
	Description    *string `db:"description"`
	User           *string `db:"user_id"`
	Association    *string `db:"sport_association_id"`
	HasAssociate   bool    `db:"has_associate"`
	AssociateFirst *string `db:"associate_first"`
	AssociateLast  *string `db:"associate_last"`
	AssociateEmail *string `db:"associate_email"`
	Denomination   *string `db:"denomination"`
	OwnerEmail     *string `db:"owner_email"`
	OnlinePayments *bool   `db:"online_payments"`
}

// EmailInvoice is the invoice invoice_send reads (scoped, live, group-aware).
type EmailInvoice struct {
	ID             string  `db:"invoice_id"`
	Association    string  `db:"sport_association_id"`
	Number         *int64  `db:"number"`
	MembershipFee  string  `db:"membership_fee"`
	ActivityFee    string  `db:"activity_fee"`
	Description    *string `db:"description"`
	DocumentToken  *string `db:"document_token"`
	HasDocumentPDF bool    `db:"has_document"`
}

// EmailInvoicePayment is the invoice's first scoped payment by key with the
// parties invoice_send takes the recipient from.
type EmailInvoicePayment struct {
	ID             string     `db:"payment_id"`
	PaymentDate    *time.Time `db:"payment_date"`
	HasAssociate   bool       `db:"has_associate"`
	AssociateFirst *string    `db:"associate_first"`
	AssociateLast  *string    `db:"associate_last"`
	AssociateEmail *string    `db:"associate_email"`
	HasUser        bool       `db:"has_user"`
	UserFirst      *string    `db:"user_first"`
	UserLast       *string    `db:"user_last"`
	UserEmail      *string    `db:"user_email"`
	HasSupplier    bool       `db:"has_supplier"`
	SupplierName   *string    `db:"supplier_name"`
	SupplierEmail  *string    `db:"supplier_email"`
}

// EmailRenderStore holds the reads and the invitation write of the three views.
type EmailRenderStore interface {
	InstanceConfig(ctx context.Context) (EmailRenderInstanceConfig, error)
	Associations(ctx context.Context, user string, impersonation *string) ([]EmailRenderAssociation, error)
	PaymentForRequest(ctx context.Context, id string, group, impersonation *string) (*EmailPaymentRequest, error)
	InvoiceForSend(ctx context.Context, id string, group, impersonation *string) (*EmailInvoice, error)
	InvoicePayment(ctx context.Context, invoice string, group, impersonation *string) (*EmailInvoicePayment, error)
	BeginCollaboratorInvite(ctx context.Context) (CollaboratorInviteTransaction, error)
}
