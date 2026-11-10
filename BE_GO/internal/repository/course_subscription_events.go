package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The events edit of CourseSubscriptionOverviewSerializer.update.

// EventInstallments is CourseSubscriptionInstallment.objects.filter(
// course_subscription=row): live, group-aware, unordered (the heap order).
func (t *courseSubscriptionTransaction) EventInstallments(ctx context.Context, row string, group *string) ([]domain.CourseSubscriptionEventInstallment, error) {
	rows := []domain.CourseSubscriptionEventInstallment{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT course_subscription_installment_id, id, payment_id, payment_date
 FROM application_coursesubscriptioninstallment WHERE course_subscription_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)
 ORDER BY ctid FOR UPDATE`, row, group)
	return rows, err
}

func (t *courseSubscriptionTransaction) PaymentPaid(ctx context.Context, payment string) (bool, error) {
	var paid bool
	err := t.tx.GetContext(ctx, &paid, `SELECT paid FROM application_payment WHERE payment_id=$1`, payment)
	return paid, err
}

func (t *courseSubscriptionTransaction) DeleteInstallment(ctx context.Context, id string, audit domain.CourseSubscriptionAudit) error {
	return t.deleteInstallments(ctx, []string{id}, true, audit)
}

func (t *courseSubscriptionTransaction) EventPaymentContext(ctx context.Context, association string) (domain.CourseSubscriptionEventContext, error) {
	writes := &courseWritesTransaction{t}
	var result domain.CourseSubscriptionEventContext
	user, err := courseWritesFirst[string](ctx, t.tx, `SELECT user_id FROM application_sportassociation WHERE sport_association_id=$1`, association)
	if err != nil {
		return result, err
	}
	result.UserID = user
	if result.AccountID, err = writes.DefaultBankAccount(ctx, &association); err != nil {
		return result, err
	}
	result.CategoryID, err = t.DefaultPaymentCategory(ctx)
	return result, err
}

// SubscriptionAssociate is instance.subscription.associate (base manager).
func (t *courseSubscriptionTransaction) SubscriptionAssociate(ctx context.Context, subscription string) (bool, *string, error) {
	rows := []struct {
		Associate *string `db:"associate_id"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT associate_id FROM application_subscription WHERE subscription_id=$1`, subscription); err != nil {
		return false, nil, err
	}
	if len(rows) == 0 {
		return false, nil, nil
	}
	return true, rows[0].Associate, nil
}

func (t *courseSubscriptionTransaction) GroupExists(ctx context.Context, id string) (bool, error) {
	return (&courseWritesTransaction{t}).GroupExists(ctx, id)
}

func (t *courseSubscriptionTransaction) CreateEventPayment(ctx context.Context, payment domain.CourseWritePayment, audit domain.CourseSubscriptionAudit) error {
	return (&courseWritesTransaction{t}).CreatePayment(ctx, payment, audit)
}

func (t *courseSubscriptionTransaction) CreateEventInstallment(ctx context.Context, installment domain.CourseWriteInstallmentCreate,
	audit domain.CourseSubscriptionAudit) error {
	return (&courseWritesTransaction{t}).CreateInstallment(ctx, installment, audit)
}
