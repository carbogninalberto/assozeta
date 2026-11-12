package repository

import (
	"context"
	"crypto/md5"
	"database/sql"
	"encoding/hex"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type InvoiceGenerations struct{ states *PaymentStates }

func NewInvoiceGenerations(states *PaymentStates) *InvoiceGenerations {
	return &InvoiceGenerations{states: states}
}

var _ domain.InvoiceGenerateStore = (*InvoiceGenerations)(nil)

// invoiceGenerateTransaction extends payment-state's transaction (payment
// lock, rendering, save defaults and audit) with the invoice generation reads and writes.
type invoiceGenerateTransaction struct{ *paymentStateTransaction }

func (r *InvoiceGenerations) BeginInvoiceGenerate(ctx context.Context) (domain.InvoiceGenerateTransaction, error) {
	tx, err := r.states.BeginPaymentState(ctx)
	if err != nil {
		return nil, err
	}
	return &invoiceGenerateTransaction{paymentStateTransaction: tx.(*paymentStateTransaction)}, nil
}

func (t *invoiceGenerateTransaction) InvoiceGeneratePayment(ctx context.Context, id string) (domain.InvoiceGeneratePayment, error) {
	var row domain.InvoiceGeneratePayment
	// payment.supplier / payment.instructor go through the base managers.
	err := t.tx.GetContext(ctx, &row, `SELECT p.amount::text AS amount, p.creation_date, p.payment_date,
 p.meta_payment_categories::text AS meta_payment_categories, s.name AS supplier_name, i.instructor_id IS NOT NULL AS has_instructor,
 i.first_name AS instructor_first_name, i.last_name AS instructor_last_name
 FROM application_payment p LEFT JOIN application_supplierandcustomers s ON s.supplier_id=p.supplier_id
 LEFT JOIN application_instructor i ON i.instructor_id=p.instructor_id WHERE p.payment_id=$1`, id)
	return row, err
}

func (t *invoiceGenerateTransaction) GenerateSubscription(ctx context.Context, id string, scope, group *string) (bool, *string, error) {
	var associate []sql.NullString
	if err := t.tx.SelectContext(ctx, &associate, `SELECT associate_id::text FROM application_subscription
 WHERE subscription_id=$1 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid)`,
		id, group, scope); err != nil {
		return false, nil, err
	}
	if len(associate) == 0 {
		return false, nil, nil
	}
	if !associate[0].Valid {
		return true, nil, nil
	}
	return true, &associate[0].String, nil
}

// InvoiceLockKey is int(hashlib.md5(str(association_id).encode()).hexdigest()[:15], 16),
// the advisory lock key Django's InvoiceService takes.
func InvoiceLockKey(association string) int64 {
	sum := md5.Sum([]byte(association))
	key, _ := strconv.ParseInt(hex.EncodeToString(sum[:])[:15], 16, 64)
	return key
}

func (t *invoiceGenerateTransaction) LockInvoiceNumbering(ctx context.Context, association string) error {
	_, err := t.tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock($1)`, InvoiceLockKey(association))
	return err
}

func (t *invoiceGenerateTransaction) InvoiceGenerateOwner(ctx context.Context, owner string) (domain.InvoiceGenerateOwner, error) {
	var row domain.InvoiceGenerateOwner
	err := t.tx.GetContext(ctx, &row, `SELECT balance_sheet_start_day, balance_sheet_start_month, starting_number_invoices,
 payment_date_equal_invoice_date FROM bakney_user WHERE user_id=$1`, owner)
	return row, err
}

func (t *invoiceGenerateTransaction) LastInvoiceNumber(ctx context.Context, query domain.InvoiceGenerateNumberQuery) (*int64, error) {
	// ORDER BY number DESC puts NULL numbers first, like Django's order_by('-number').
	statement := `SELECT v.number FROM application_invoice v JOIN application_payment p ON p.invoice_id=v.invoice_id
 WHERE v.sport_association_id=$1 AND NOT v.archived AND NOT v.deleted AND ($2::uuid IS NULL OR v.group_id=$2::uuid)
 AND p.creation_date >= $3 ORDER BY v.number DESC LIMIT 1`
	if query.Before {
		statement = `SELECT v.number FROM application_invoice v
 WHERE v.sport_association_id=$1 AND NOT v.archived AND NOT v.deleted AND ($2::uuid IS NULL OR v.group_id=$2::uuid)
 AND v.creation_date < $3 ORDER BY v.number DESC LIMIT 1`
	}
	var number sql.NullInt64
	err := t.tx.GetContext(ctx, &number, statement, query.Association, query.Group, query.From)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && !number.Valid) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &number.Int64, nil
}

func (t *invoiceGenerateTransaction) AssociationDenomination(ctx context.Context, association string) (*string, error) {
	var denomination *string
	err := t.tx.GetContext(ctx, &denomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return denomination, err
}

func (t *invoiceGenerateTransaction) AssociateNames(ctx context.Context, associate string) (*string, *string, error) {
	var row struct {
		First *string `db:"first_name"`
		Last  *string `db:"last_name"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT first_name, last_name FROM application_associate WHERE associate_id=$1`, associate)
	return row.First, row.Last, err
}

// SubscriptionByPayment is Subscription.objects.filter(payment=payment).first() (X-Group-Id aware).
func (t *invoiceGenerateTransaction) SubscriptionByPayment(ctx context.Context, payment string, group *string) (*string,
	*domain.InvoiceGeneratePeriod, error) {
	var row struct {
		ID string `db:"subscription_id"`
		domain.InvoiceGeneratePeriod
	}
	err := t.tx.GetContext(ctx, &row, `SELECT subscription_id, start_date, end_date FROM application_subscription
 WHERE payment_id=$1 AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY subscription_id LIMIT 1`, payment, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}
	return &row.ID, &row.InvoiceGeneratePeriod, nil
}

func (t *invoiceGenerateTransaction) FirstMembership(ctx context.Context, subscription string) (*domain.InvoiceGenerateMembership, error) {
	var row domain.InvoiceGenerateMembership
	err := t.tx.GetContext(ctx, &row, `SELECT start_date, end_date, membership_type FROM application_subscriptionmembership
 WHERE subscription_id=$1 ORDER BY subscription_membership_id LIMIT 1`, subscription)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// CourseTitle follows the description's course branch: the first course
// subscription (all rows), else the first installment (X-Group-Id aware,
// deleted included), else the first carnet subscription paid by the payment.
func (t *invoiceGenerateTransaction) CourseTitle(ctx context.Context, payment string, group *string) (domain.InvoiceGenerateTitle, error) {
	queries := []struct {
		sql   string
		group bool
	}{
		{`SELECT c.title, true AS present FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE cs.payment_id=$1 ORDER BY cs.course_subscription_id LIMIT 1`, false},
		{`SELECT c.title, true AS present FROM application_coursesubscriptioninstallment n
 JOIN application_coursesubscription cs ON cs.course_subscription_id=n.course_subscription_id
 JOIN application_course c ON c.course_id=cs.course_id
 WHERE n.payment_id=$1 AND ($2::uuid IS NULL OR n.group_id=$2::uuid) ORDER BY n.course_subscription_installment_id LIMIT 1`, true},
		{`SELECT k.title, k.carnet_id IS NOT NULL AS present FROM application_carnetsubscription cs
 LEFT JOIN application_carnet k ON k.carnet_id=cs.carnet_id_id
 WHERE cs.payment_id=$1 ORDER BY cs.carnet_subscription_id LIMIT 1`, false},
	}
	for _, query := range queries {
		var row struct {
			Title   *string `db:"title"`
			Present bool    `db:"present"`
		}
		args := []any{payment}
		if query.group {
			args = append(args, group)
		}
		err := t.tx.GetContext(ctx, &row, query.sql, args...)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return domain.InvoiceGenerateTitle{}, err
		}
		return domain.InvoiceGenerateTitle{Found: true, Missing: !row.Present, Title: row.Title}, nil
	}
	return domain.InvoiceGenerateTitle{}, nil
}

func (t *invoiceGenerateTransaction) CreateInvoice(ctx context.Context, create domain.InvoiceGenerateCreate, overrides map[string]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_invoice (invoice_id, document_pdf_id, sport_association_id, creation_date,
 membership_fee, activity_fee, meta_payment_categories, description, number, archived, cancelled, selected_tutor_id, meta,
 imported_from_associami, group_id, deleted)
 VALUES ($1, NULL, $2, $3, $4::numeric, $5::numeric, $6::jsonb, $7, $8, false, false, NULL, NULL, false, $9, false)`,
		create.ID, create.Association, create.CreationDate, create.MembershipFee, create.ActivityFee, create.MetaPaymentCategories,
		create.Description, create.Number, create.Group); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_invoice", "invoice_id", create.ID)
	if err != nil {
		return err
	}
	for field, value := range overrides {
		changes[field] = [2]string{"None", value}
	}
	path, association := "direct", create.Association
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "invoice", Key: create.ID, Representation: "Invoice object (" + create.ID + ")",
		Action: 0, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *invoiceGenerateTransaction) SetInvoiceCreationDate(ctx context.Context, invoice, association string, date time.Time,
	changes map[string][2]string, audit domain.LoginAuditContext, at time.Time) error {
	if len(changes) > 0 {
		path := "direct"
		if err := appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "invoice", Key: invoice, Representation: "Invoice object (" + invoice + ")",
			Action: 1, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
			return err
		}
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_invoice SET creation_date=$2 WHERE invoice_id=$1`, invoice, date)
	return err
}

func (t *invoiceGenerateTransaction) SaveGeneratedPayment(ctx context.Context, save domain.InvoiceGenerateSave) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET paid=true, associate_id=$2, invoice_id=$3, payment_category_id=$4,
 description=$5 WHERE payment_id=$1`, save.ID, save.Associate, save.Invoice, save.CategoryID, save.Description)
	return err
}
