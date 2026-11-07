package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type PaymentStates struct{ db *sqlx.DB }

func NewPaymentStates(db *sqlx.DB) *PaymentStates { return &PaymentStates{db: db} }

var _ domain.PaymentStateStore = (*PaymentStates)(nil)

// paymentStateTransaction shares one transaction with the payment archive
// save helpers and the course subscription payment rendering and deletes.
type paymentStateTransaction struct {
	*paymentArchiveTransaction
	courses *courseSubscriptionTransaction
}

func (r *PaymentStates) BeginPaymentState(ctx context.Context) (domain.PaymentStateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	base := &paymentCategoryTransaction{ownedTransaction: tx}
	return &paymentStateTransaction{paymentArchiveTransaction: &paymentArchiveTransaction{paymentCategoryTransaction: base},
		courses: &courseSubscriptionTransaction{paymentCategoryTransaction: base}}, nil
}

func (t *paymentStateTransaction) ScopedAssociation(ctx context.Context, owner string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, scope, false)
}

func (t *paymentStateTransaction) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	return t.courses.ActorAssociation(ctx, actor)
}

func (t *paymentStateTransaction) PaymentState(ctx context.Context, query domain.PaymentStateQuery) (*domain.PaymentStateRow, error) {
	var row domain.PaymentStateRow
	err := t.tx.GetContext(ctx, &row, `SELECT p.payment_id, COALESCE(p.sport_association_id::text, '') AS sport_association_id, p.subject,
 p.archived, p.payment_category_id, c.name AS payment_category_name, p.description, p.meta::text AS meta, p.course::text AS course, p.paid,
 p.expense, p.payment_date, p.invoice_id, p.associate_id, p.supplier_id, p.instructor_id
 FROM application_payment p LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id
 WHERE p.payment_id=$1 AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR p.sport_association_id=$3::uuid) AND ($4::uuid IS NULL OR p.sport_association_id=$4::uuid)
 FOR UPDATE OF p`, query.ID, query.Group, query.Association, query.Scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *paymentStateTransaction) RenderPayment(ctx context.Context, id string, group *string) (domain.PaymentStateRender, error) {
	payment, err := t.courses.payment(ctx, id, group)
	if err != nil {
		return domain.PaymentStateRender{}, err
	}
	render := domain.PaymentStateRender{Payment: payment}
	if payment.InstructorID != nil {
		rows := []domain.InstructorRow{}
		if err = t.tx.SelectContext(ctx, &rows, instructorColumns+` WHERE i.instructor_id=$1`, *payment.InstructorID); err != nil {
			return render, err
		}
		if len(rows) == 1 {
			render.Instructor = &rows[0]
		}
	}
	return render, nil
}

// SavePaymentState is Payment.save(): only the columns the views change differ
// from the stored row.
func (t *paymentStateTransaction) SavePaymentState(ctx context.Context, save domain.PaymentStateSave) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET paid=$2, payment_date=$3, payment_category_id=$4, description=$5
 WHERE payment_id=$1`, save.ID, save.Paid, save.PaymentDate, save.CategoryID, save.Description)
	return err
}

func (t *paymentStateTransaction) SupplierInvoice(ctx context.Context, payment string) (*domain.PaymentStateSupplierInvoice, error) {
	var row domain.PaymentStateSupplierInvoice
	err := t.tx.GetContext(ctx, &row, `SELECT invoice_supplier_id, sport_association_id, paid, payment_date FROM application_invoicesuppliers
 WHERE payment_id=$1 ORDER BY invoice_supplier_id LIMIT 1 FOR UPDATE`, payment)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *paymentStateTransaction) SaveSupplierInvoice(ctx context.Context, id string, paid bool, paymentDate *time.Time) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_invoicesuppliers SET paid=$2, payment_date=$3 WHERE invoice_supplier_id=$1`,
		id, paid, paymentDate)
	return err
}

// AppendSupplierInvoiceAudit is the save's update entry, indexed directly to
// the invoice's association.
func (t *paymentStateTransaction) AppendSupplierInvoiceAudit(ctx context.Context, invoice domain.PaymentStateSupplierInvoice,
	changes map[string][2]string, audit domain.LoginAuditContext, at time.Time) error {
	if len(changes) == 0 {
		return nil
	}
	path, association := "direct", invoice.AssociationID
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "invoicesuppliers", Key: invoice.ID,
		Representation: "InvoiceSuppliers object (" + invoice.ID + ")", Action: 1, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *paymentStateTransaction) DeletePayment(ctx context.Context, id string, audit domain.CourseSubscriptionAudit) error {
	snapshot, err := t.courses.snapshot(ctx, "application_payment", "payment_id", id)
	if err != nil {
		return err
	}
	if snapshot == nil {
		return domain.ErrPaymentStateServer
	}
	return t.courses.deletePayments(ctx, []*courseSubscriptionSnapshot{snapshot}, true, audit)
}

var paymentStateMarkColumns = map[string]bool{"archived": true, "deleted": true}

func (t *paymentStateTransaction) MarkPayments(ctx context.Context, column string, ids []string, association string, scope, group *string) error {
	if !paymentStateMarkColumns[column] {
		return domain.ErrPaymentStateServer
	}
	if len(ids) == 0 {
		return nil
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET `+column+`=true
 WHERE payment_id=ANY($1::uuid[]) AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid)
 AND ($4::uuid IS NULL OR sport_association_id=$4::uuid)`, ids, association, group, scope)
	return err
}
