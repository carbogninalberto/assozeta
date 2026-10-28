package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type PaymentArchives struct{ db *sqlx.DB }

func NewPaymentArchives(db *sqlx.DB) *PaymentArchives { return &PaymentArchives{db: db} }

var _ domain.PaymentArchiveStore = (*PaymentArchives)(nil)

func (r *PaymentArchives) BeginPaymentArchive(ctx context.Context) (domain.PaymentArchiveTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &paymentArchiveTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

type paymentArchiveTransaction struct{ *paymentCategoryTransaction }

func (t *paymentArchiveTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *paymentArchiveTransaction) PaymentForArchive(ctx context.Context, id, association string, group *string) (domain.PaymentSaveRow, error) {
	var row domain.PaymentSaveRow
	err := t.tx.GetContext(ctx, &row, `SELECT p.payment_id, p.sport_association_id, p.subject, p.archived, p.payment_category_id,
 c.name AS payment_category_name, p.description, p.meta::text AS meta, p.course::text AS course
 FROM application_payment p LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id
 WHERE p.payment_id=$1 AND p.sport_association_id=$2 AND NOT p.deleted AND ($3::uuid IS NULL OR p.group_id=$3::uuid)
 FOR UPDATE OF p`, id, association, group)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrPaymentNotFound
	}
	return row, err
}

// DefaultPaymentCategory is PaymentCategory.objects.filter(name__iexact=
// 'entrate e proventi da attività tipiche').first() (by key).
func (t *paymentArchiveTransaction) DefaultPaymentCategory(ctx context.Context) (*string, *string, error) {
	var row struct {
		ID   string `db:"payment_category_id"`
		Name string `db:"name"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT payment_category_id, name FROM application_paymentcategory
 WHERE UPPER(name::text)=UPPER('entrate e proventi da attività tipiche') ORDER BY payment_category_id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}
	return &row.ID, &row.Name, nil
}

func (t *paymentArchiveTransaction) PaymentLinkedName(ctx context.Context, payment string) (domain.PaymentLinkedName, error) {
	for _, query := range []string{
		`SELECT c.title FROM application_coursesubscription s JOIN application_course c ON c.course_id=s.course_id
 WHERE s.payment_id=$1 ORDER BY s.course_subscription_id LIMIT 1`,
		`SELECT c.title FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription s ON s.course_subscription_id=i.course_subscription_id
 JOIN application_course c ON c.course_id=s.course_id WHERE i.payment_id=$1 ORDER BY i.course_subscription_installment_id LIMIT 1`,
		`SELECT k.title FROM application_carnetsubscription s JOIN application_carnet k ON k.carnet_id=s.carnet_id_id
 WHERE s.payment_id=$1 ORDER BY s.carnet_subscription_id LIMIT 1`,
	} {
		var title *string
		err := t.tx.GetContext(ctx, &title, query, payment)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return domain.PaymentLinkedName{}, err
		}
		return domain.PaymentLinkedName{Found: true, Title: title}, nil
	}
	return domain.PaymentLinkedName{}, nil
}

func (t *paymentArchiveTransaction) SavePaymentArchive(ctx context.Context, id string, archived bool, category, description *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET archived=$2, payment_category_id=$3, description=$4 WHERE payment_id=$1`,
		id, archived, category, description)
	return err
}

func (t *paymentArchiveTransaction) AppendPaymentAudit(ctx context.Context, event domain.PaymentArchiveEvent) error {
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "payment", Key: event.PaymentID,
		Representation: "Payment object (" + event.PaymentID + ")", Action: 1, Changes: event.Changes, Context: event.Context,
		AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At})
}
