package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// StripeRoutes reads payments for the stripe views (each Django query is its
// own autocommit statement) and opens short transactions for their saves.
type StripeRoutes struct {
	db       *sqlx.DB
	payments *PaymentStates
}

func NewStripeRoutes(db *sqlx.DB) *StripeRoutes {
	return &StripeRoutes{db: db, payments: NewPaymentStates(db)}
}

var _ domain.StripeRoutesStore = (*StripeRoutes)(nil)

// stripePaymentColumns: the payment, its category name (Payment.save()
// defaults), user, associate, association (base managers) and owner.
const stripePaymentColumns = `SELECT p.payment_id, COALESCE(p.sport_association_id::text, '') AS sport_association_id, p.subject, p.archived,
 p.payment_category_id, c.name AS payment_category_name, p.description, p.meta::text AS meta, p.course::text AS course,
 p.amount::text AS amount, p.paid, p.payment_date, p.payment_intent_id, p.invoice_id, p.user_id, u.first_name AS user_first_name,
 u.last_name AS user_last_name, p.associate_id, a.first_name AS associate_first_name, a.last_name AS associate_last_name,
 a.user_id AS associate_user_id, s.sport_association_id IS NOT NULL AS has_association, s.denomination,
 s.stripe_available_methods::text AS stripe_available_methods, o.online_payments AS owner_online_payments
 FROM application_payment p
 LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id
 LEFT JOIN bakney_user u ON u.user_id=p.user_id
 LEFT JOIN application_associate a ON a.associate_id=p.associate_id
 LEFT JOIN application_sportassociation s ON s.sport_association_id=p.sport_association_id
 LEFT JOIN bakney_user o ON o.user_id=s.user_id`

// stripePaymentScope is Payment.objects (live, X-Group-Id) plus scoped_queryset's association.
const stripePaymentScope = ` NOT p.deleted AND ($1::uuid IS NULL OR p.group_id=$1::uuid) AND ($2::uuid IS NULL OR p.sport_association_id=$2::uuid)`

func (r *StripeRoutes) first(ctx context.Context, condition string, value any, query domain.StripePaymentQuery) (*domain.StripePayment, error) {
	var row domain.StripePayment
	err := r.db.GetContext(ctx, &row, stripePaymentColumns+` WHERE`+stripePaymentScope+` AND `+condition+` ORDER BY p.payment_id LIMIT 1`,
		query.Group, query.Scope, value)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *StripeRoutes) PaymentByID(ctx context.Context, id string, query domain.StripePaymentQuery) (*domain.StripePayment, error) {
	return r.first(ctx, `p.payment_id=$3::uuid`, id, query)
}

func (r *StripeRoutes) PaymentByIntent(ctx context.Context, intent string, query domain.StripePaymentQuery) (*domain.StripePayment, error) {
	return r.first(ctx, `p.payment_intent_id=$3`, intent, query)
}

func (r *StripeRoutes) PaymentsByIntent(ctx context.Context, match domain.StripeIntentMatch, query domain.StripePaymentQuery) ([]domain.StripePayment, error) {
	rows := []domain.StripePayment{}
	if match.NoMatch {
		// str() of a value that carries an address equals no stored intent.
		return rows, nil
	}
	var err error
	if match.Null {
		err = r.db.SelectContext(ctx, &rows, stripePaymentColumns+` WHERE`+stripePaymentScope+` AND p.payment_intent_id IS NULL`,
			query.Group, query.Scope)
	} else {
		err = r.db.SelectContext(ctx, &rows, stripePaymentColumns+` WHERE`+stripePaymentScope+` AND p.payment_intent_id=$3`,
			query.Group, query.Scope, match.Value)
	}
	return rows, err
}

func (r *StripeRoutes) PaymentIDs(ctx context.Context, ids []string, unpaidOnly bool, query domain.StripePaymentQuery) ([]string, error) {
	values := []string{}
	if len(ids) == 0 {
		return values, nil
	}
	paid := ``
	if unpaidOnly {
		paid = ` AND NOT p.paid`
	}
	err := r.db.SelectContext(ctx, &values, `SELECT p.payment_id::text FROM application_payment p WHERE`+stripePaymentScope+
		` AND p.payment_id = ANY($3::uuid[])`+paid, query.Group, query.Scope, ids)
	return values, err
}

func (r *StripeRoutes) AssociationCheckout(ctx context.Context, id string, scope *string) (bool, *string, error) {
	rows := []struct {
		Checkout *string `db:"checkout_info"`
	}{}
	err := r.db.SelectContext(ctx, &rows, `SELECT checkout_info FROM application_sportassociation
 WHERE sport_association_id=$1::uuid AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid)
 ORDER BY sport_association_id LIMIT 1`, id, scope)
	if err != nil || len(rows) == 0 {
		return false, nil, err
	}
	return true, rows[0].Checkout, nil
}

type stripeRoutesTransaction struct{ *paymentStateTransaction }

func (r *StripeRoutes) BeginStripe(ctx context.Context) (domain.StripeRoutesTransaction, error) {
	tx, err := r.payments.BeginPaymentState(ctx)
	if err != nil {
		return nil, err
	}
	return &stripeRoutesTransaction{paymentStateTransaction: tx.(*paymentStateTransaction)}, nil
}

func (t *stripeRoutesTransaction) LockStripePayment(ctx context.Context, id string, group *string) (*domain.StripePayment, error) {
	var row domain.StripePayment
	err := t.tx.GetContext(ctx, &row, stripePaymentColumns+` WHERE NOT p.deleted AND ($1::uuid IS NULL OR p.group_id=$1::uuid)
 AND p.payment_id=$2::uuid FOR UPDATE OF p`, group, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *stripeRoutesTransaction) SavePaymentIntent(ctx context.Context, id string, intent, category, description *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET payment_intent_id=$2, payment_category_id=$3, description=$4
 WHERE payment_id=$1`, id, intent, category, description)
	return err
}
