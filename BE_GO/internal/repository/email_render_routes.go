package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// EmailRenderRoutes serves collaborators/add, payment/<uid>/request and
// invoice/<uid>/send.
type EmailRenderRoutes struct{ db *sqlx.DB }

func NewEmailRenderRoutes(db *sqlx.DB) *EmailRenderRoutes { return &EmailRenderRoutes{db: db} }

var _ domain.EmailRenderStore = (*EmailRenderRoutes)(nil)

// InstanceConfig is InstanceConfiguration.objects.first() (by primary key).
func (r *EmailRenderRoutes) InstanceConfig(ctx context.Context) (domain.EmailRenderInstanceConfig, error) {
	var config domain.EmailRenderInstanceConfig
	err := r.db.GetContext(ctx, &config, `SELECT true AS found,self_hosted,primary_color,name,logo_path,updated_at
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.EmailRenderInstanceConfig{}, nil
	}
	return config, err
}

const emailRenderAssociations = `SELECT a.sport_association_id::text AS sport_association_id,a.denomination,u.email AS owner_email,
 u.online_payments FROM application_sportassociation a LEFT JOIN bakney_user u ON u.user_id=a.user_id
 WHERE a.user_id=$1 AND NOT a.deleted AND ($2::uuid IS NULL OR a.sport_association_id=$2::uuid) ORDER BY a.sport_association_id`

// Associations is (scoped_queryset(SportAssociation.objects)).filter(user=...):
// live rows, limited to the impersonated association when there is one.
func (r *EmailRenderRoutes) Associations(ctx context.Context, user string, impersonation *string) ([]domain.EmailRenderAssociation, error) {
	rows := []domain.EmailRenderAssociation{}
	err := r.db.SelectContext(ctx, &rows, emailRenderAssociations, user, impersonation)
	return rows, err
}

// PaymentForRequest is scoped_queryset(Payment.objects).filter(payment_id=uid).first():
// live, X-Group-Id-aware, the impersonated association; related rows through
// the base managers.
func (r *EmailRenderRoutes) PaymentForRequest(ctx context.Context, id string, group, impersonation *string) (*domain.EmailPaymentRequest, error) {
	var row domain.EmailPaymentRequest
	err := r.db.GetContext(ctx, &row, `SELECT p.payment_id::text AS payment_id,p.paid,p.subject,p.amount::text AS amount,p.description,
 p.user_id::text AS user_id,p.sport_association_id::text AS sport_association_id,p.associate_id IS NOT NULL AS has_associate,
 a.first_name AS associate_first,a.last_name AS associate_last,a.email AS associate_email,s.denomination,u.email AS owner_email,u.online_payments
 FROM application_payment p LEFT JOIN application_associate a ON a.associate_id=p.associate_id
 LEFT JOIN application_sportassociation s ON s.sport_association_id=p.sport_association_id LEFT JOIN bakney_user u ON u.user_id=s.user_id
 WHERE p.payment_id=$1 AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR p.sport_association_id=$3::uuid) ORDER BY p.payment_id LIMIT 1`, id, group, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// InvoiceForSend is scoped_queryset(Invoice.objects).filter(invoice_id=uid).first().
func (r *EmailRenderRoutes) InvoiceForSend(ctx context.Context, id string, group, impersonation *string) (*domain.EmailInvoice, error) {
	var row domain.EmailInvoice
	err := r.db.GetContext(ctx, &row, `SELECT i.invoice_id::text AS invoice_id,i.sport_association_id::text AS sport_association_id,i.number,
 i.membership_fee::text AS membership_fee,i.activity_fee::text AS activity_fee,i.description,d.token::text AS document_token,
 i.document_pdf_id IS NOT NULL AS has_document
 FROM application_invoice i LEFT JOIN docmanager_document d ON d.document_id=i.document_pdf_id
 WHERE i.invoice_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR i.sport_association_id=$3::uuid) ORDER BY i.invoice_id LIMIT 1`, id, group, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// InvoicePayment is scoped_queryset(Payment.objects).filter(invoice=invoice).first()
// (ordered by key), with its associate, user and supplier.
func (r *EmailRenderRoutes) InvoicePayment(ctx context.Context, invoice string, group, impersonation *string) (*domain.EmailInvoicePayment, error) {
	var row domain.EmailInvoicePayment
	err := r.db.GetContext(ctx, &row, `SELECT p.payment_id::text AS payment_id,p.payment_date,
 p.associate_id IS NOT NULL AS has_associate,a.first_name AS associate_first,a.last_name AS associate_last,a.email AS associate_email,
 p.user_id IS NOT NULL AS has_user,u.first_name AS user_first,u.last_name AS user_last,u.email AS user_email,
 p.supplier_id IS NOT NULL AS has_supplier,s.name AS supplier_name,s.email AS supplier_email
 FROM application_payment p LEFT JOIN application_associate a ON a.associate_id=p.associate_id
 LEFT JOIN bakney_user u ON u.user_id=p.user_id LEFT JOIN application_supplierandcustomers s ON s.supplier_id=p.supplier_id
 WHERE p.invoice_id=$1 AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR p.sport_association_id=$3::uuid) ORDER BY p.payment_id LIMIT 1`, invoice, group, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *EmailRenderRoutes) BeginCollaboratorInvite(ctx context.Context) (domain.CollaboratorInviteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &collaboratorInviteTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

type collaboratorInviteTransaction struct{ *dashboardLayoutTransaction }

var _ domain.CollaboratorInviteTransaction = (*collaboratorInviteTransaction)(nil)

func (t *collaboratorInviteTransaction) CreateInvite(ctx context.Context, invite domain.CollaborationInvite) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_collaborationinvites
 (collaboration_invite_id,user_id,email,creation_date,accepted,expiration_date,token,collaborator_role,collaborator_permissions)
 VALUES($1,$2,$3,$4,false,$5,$6,$7,$8::jsonb)`, invite.ID, invite.User, invite.Email, invite.Created, invite.Expiration, invite.Token,
		invite.Role, invite.Permissions)
	return err
}

// AppendInviteAudit writes auditlog's create entry rendered from the stored row.
func (t *collaboratorInviteTransaction) AppendInviteAudit(ctx context.Context, id string, event domain.CollaborationInviteAudit) error {
	changes, err := createdModelChanges(ctx, t.tx, "application_collaborationinvites", "collaboration_invite_id", id)
	if err != nil {
		return err
	}
	differs := event.PermissionsMemory != nil && event.PermissionsLoaded != nil && *event.PermissionsMemory != *event.PermissionsLoaded
	if differs {
		// The create entry renders the instance's own value, not the stored reading.
		changes["collaborator_permissions"] = [2]string{"None", *event.PermissionsMemory}
	}
	entry := modelAuditEntry{Model: "collaborationinvites", Key: id,
		Representation: "CollaborationInvites object (" + id + ")", Action: 0, Changes: changes, Context: event.Context,
		AssociationID: event.AssociationID, ResolutionPath: event.ResolutionPath, At: event.At}
	if err = appendModelAudit(ctx, t.tx, entry); err != nil || !differs {
		return err
	}
	entry.Action = 1
	entry.Changes = map[string][2]string{"collaborator_permissions": {*event.PermissionsLoaded, *event.PermissionsMemory}}
	return appendModelAudit(ctx, t.tx, entry)
}

func (t *collaboratorInviteTransaction) LiveAssociations(ctx context.Context, user string, impersonation *string) ([]domain.EmailRenderAssociation, error) {
	rows := []domain.EmailRenderAssociation{}
	err := t.tx.SelectContext(ctx, &rows, emailRenderAssociations, user, impersonation)
	return rows, err
}
