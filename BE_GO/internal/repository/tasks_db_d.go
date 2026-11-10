package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// TasksDBD stores change_type_of_stripe_payments, generate_coupon_if_not_exists
// and renew_memberships_payments.
type TasksDBD struct{ db *sqlx.DB }

func NewTasksDBD(db *sqlx.DB) *TasksDBD { return &TasksDBD{db: db} }

func (r *TasksDBD) BeginTasksDBD(ctx context.Context, job domain.Job) (domain.TasksDBDTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &tasksDBDTransaction{ownedTransaction: tx}, nil
}

type tasksDBDTransaction struct{ *ownedTransaction }

// --- change_type_of_stripe_payments -------------------------------------------

// tasksDBDStripeTypeWhere is Payment.objects (not deleted, no group header)
// filtered by type 'default', a stored intent, paid, and created after or paid
// on or after the cutoff.
const tasksDBDStripeTypeWhere = ` WHERE NOT p.deleted AND p.paid AND p.payment_intent_id IS NOT NULL AND p.type='default'
 AND (p.creation_date>$1 OR p.payment_date>=$1)`

func (t *tasksDBDTransaction) StripeTypeCount(ctx context.Context, cutoff time.Time) (int, error) {
	var count int
	err := t.tx.GetContext(ctx, &count, `SELECT COUNT(*) FROM application_payment p`+tasksDBDStripeTypeWhere, cutoff)
	return count, err
}

// StripeTypeBatch is payments[offset:offset+limit] of the select_related
// queryset: the same joins and no ORDER BY, as Django slices it.
func (t *tasksDBDTransaction) StripeTypeBatch(ctx context.Context, cutoff time.Time, offset, limit int) ([]domain.StripeTypeCandidate, error) {
	rows := []domain.StripeTypeCandidate{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT p.payment_id, p.payment_intent_id FROM application_payment p
 LEFT OUTER JOIN application_sportassociation a ON p.sport_association_id=a.sport_association_id
 LEFT OUTER JOIN bakney_user u ON a.user_id=u.user_id`+tasksDBDStripeTypeWhere+` LIMIT $2 OFFSET $3`, cutoff, limit, offset)
	return rows, err
}

// MarkStripeType is Payment.objects.bulk_update(payments, ['type']) with
// type 'stripe': no signals, no audit.
func (t *tasksDBDTransaction) MarkStripeType(ctx context.Context, ids []uuid.UUID) error {
	if len(ids) == 0 {
		return nil
	}
	placeholders := make([]string, len(ids))
	arguments := make([]any, len(ids))
	for i, id := range ids {
		placeholders[i] = "$" + strconv.Itoa(i+1) + "::uuid"
		arguments[i] = id.String()
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET type='stripe' WHERE NOT deleted AND payment_id IN (`+
		strings.Join(placeholders, ",")+`)`, arguments...)
	return err
}

// --- generate_coupon_if_not_exists ----------------------------------------------

// CouponAssociations is SportAssociation.objects (not deleted) without an
// affiliate code, unordered as the task's iterator.
func (t *tasksDBDTransaction) CouponAssociations(ctx context.Context) ([]domain.CouponAssociation, error) {
	rows := []domain.CouponAssociation{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT sport_association_id, denomination FROM application_sportassociation
 WHERE NOT deleted AND affiliate_code IS NULL`)
	return rows, err
}

// SaveCoupon is sport_association.save() after setting affiliate_code and
// affiliate_code_stripe: auditlog compares the stored row (default manager)
// with the instance and indexes the association itself; then the UPDATE.
func (t *tasksDBDTransaction) SaveCoupon(ctx context.Context, association domain.CouponAssociation, code, coupon string, at time.Time) error {
	var stored struct {
		Code   *string `db:"affiliate_code"`
		Stripe *string `db:"affiliate_code_stripe"`
	}
	if err := t.tx.GetContext(ctx, &stored, `SELECT affiliate_code, affiliate_code_stripe FROM application_sportassociation
 WHERE sport_association_id=$1 FOR UPDATE`, association.ID); err != nil {
		return err
	}
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	changes := map[string][2]string{}
	if text(stored.Code) != code {
		changes["affiliate_code"] = [2]string{text(stored.Code), code}
	}
	if text(stored.Stripe) != coupon {
		changes["affiliate_code_stripe"] = [2]string{text(stored.Stripe), coupon}
	}
	if len(changes) > 0 {
		id, path := association.ID.String(), "self"
		if err := appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "sportassociation", Key: id,
			Representation: "SportAssociation object (" + id + ")", Action: 1, Changes: changes,
			AssociationID: &id, ResolutionPath: &path, At: at}); err != nil {
			return err
		}
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET affiliate_code=$2, affiliate_code_stripe=$3
 WHERE sport_association_id=$1`, association.ID, code, coupon)
	return err
}

// --- renew_memberships_payments ---------------------------------------------------

// RenewalCandidates is the task's select_related queryset (membership type,
// active, auto renewal, due, positive fee, not deleted, active course), unordered.
func (t *tasksDBDTransaction) RenewalCandidates(ctx context.Context, now time.Time) ([]domain.RenewalCandidate, error) {
	rows := []domain.RenewalCandidate{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT cs.course_subscription_id, cs.billed_until, cs.billed_frequency, cs.subscription_id
 FROM application_coursesubscription cs INNER JOIN application_course c ON cs.course_id=c.course_id
 LEFT OUTER JOIN application_sportassociation a ON c.sport_association_id=a.sport_association_id
 LEFT OUTER JOIN bakney_user u ON a.user_id=u.user_id
 LEFT OUTER JOIN application_subscription s ON cs.subscription_id=s.subscription_id
 LEFT OUTER JOIN application_associate t ON s.associate_id=t.associate_id
 WHERE NOT cs.deleted AND cs.auto_renewal AND cs.billed_until<=$1 AND c.status_flag=2 AND NOT cs.deleted
 AND cs.membership_active AND cs.membership_fee>0 AND cs.type=3`, now)
	return rows, err
}

// RenewalExisting is Payment.objects.filter(meta__course_subscription_id=...,
// meta__billed_from=..., meta__billed_until=...).first().
func (t *tasksDBDTransaction) RenewalExisting(ctx context.Context, row uuid.UUID, from, until string) (*domain.RenewalExisting, error) {
	var existing domain.RenewalExisting
	err := t.tx.GetContext(ctx, &existing, `SELECT payment_id, paid FROM application_payment WHERE NOT deleted
 AND (meta -> 'billed_from')=to_jsonb($2::text) AND (meta -> 'billed_until')=to_jsonb($3::text)
 AND (meta -> 'course_subscription_id')=to_jsonb($1::text) ORDER BY payment_id LIMIT 1`, row.String(), from, until)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &existing, err
}

// LockRenewal is CourseSubscription.objects.select_for_update(nowait=True).get(pk=...).
func (t *tasksDBDTransaction) LockRenewal(ctx context.Context, id uuid.UUID) (domain.RenewalRow, error) {
	var row domain.RenewalRow
	err := t.tx.GetContext(ctx, &row, `SELECT course_subscription_id, course_id, billed_until, billed_frequency,
 membership_fee::text AS membership_fee, subscription_id FROM application_coursesubscription
 WHERE NOT deleted AND course_subscription_id=$1 FOR UPDATE NOWAIT`, id)
	var pg *pgconn.PgError
	switch {
	case errors.As(err, &pg) && pg.Code == "55P03":
		return row, domain.ErrRenewalLocked
	case errors.Is(err, sql.ErrNoRows):
		return row, domain.ErrRenewalMissing
	}
	return row, err
}

// RenewalRelations loads course, course.sport_association(.user) and
// subscription.associate through the forward relations (base managers).
func (t *tasksDBDTransaction) RenewalRelations(ctx context.Context, row domain.RenewalRow) (domain.RenewalRelations, error) {
	var relations domain.RenewalRelations
	err := t.tx.GetContext(ctx, &relations, `SELECT c.title, a.sport_association_id, a.user_id,
 s.subscription_id IS NOT NULL AS has_subscription, s.associate_id
 FROM application_course c LEFT JOIN application_sportassociation a ON a.sport_association_id=c.sport_association_id
 LEFT JOIN application_subscription s ON s.subscription_id=$2::uuid WHERE c.course_id=$1`, row.CourseID, row.SubscriptionID)
	return relations, err
}

// CreateRenewalPayment is Payment.objects.create(...): PaymentManager.create
// sets the association's default bank account for a payment with a user,
// Payment.save() the default category; auditlog's create entry is indexed
// directly through the payment's association.
func (t *tasksDBDTransaction) CreateRenewalPayment(ctx context.Context, payment domain.RenewalPayment, at time.Time) error {
	var account, category *string
	if payment.OwnerID != nil {
		var id string
		err := t.tx.GetContext(ctx, &id, `SELECT custom_account_id FROM application_customaccounts
 WHERE account_type=2 AND NOT editable AND enabled AND sport_association_id=$1 ORDER BY custom_account_id LIMIT 1`, payment.AssociationID)
		if err == nil {
			account = &id
		} else if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	var row struct {
		ID   string `db:"payment_category_id"`
		Name string `db:"name"`
	}
	err := t.tx.GetContext(ctx, &row, secCategoryDefaultSQL, payment.AssociationID)
	id := row.ID
	if err == nil {
		category = &id
	} else if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date, paid,
 expense, description, payment_category_id, user_id, associate_id, sport_association_id, archived, meta, custom_accounts_id, deleted)
 VALUES ($1,'default',2,$2::numeric,$3,$3,false,false,$4,$5,$6,$7,$8,false,$9::jsonb,$10,false)`, payment.ID, payment.Amount,
		payment.BilledUntil, payment.Description, category, payment.OwnerID, payment.AssociateID, payment.AssociationID, payment.Meta,
		account); err != nil {
		return err
	}
	key := payment.ID.String()
	changes, err := createdModelChanges(ctx, t.tx, "application_payment", "payment_id", key)
	if err != nil {
		return err
	}
	association, path := payment.AssociationID.String(), "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "payment", Key: key, Representation: "Payment object (" + key + ")",
		Action: 0, Changes: changes, AssociationID: &association, ResolutionPath: &path, At: at})
}

// AdvanceRenewal is CourseSubscription.objects.filter(pk=...).update(billed_until=...).
func (t *tasksDBDTransaction) AdvanceRenewal(ctx context.Context, id uuid.UUID, until time.Time) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscription SET billed_until=$2 WHERE NOT deleted AND course_subscription_id=$1`,
		id, until)
	return err
}

// AddMembershipPayment is membership_payments.add(payment) (not audited).
func (t *tasksDBDTransaction) AddMembershipPayment(ctx context.Context, row, payment uuid.UUID) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_coursesubscription_membership_payments (coursesubscription_id, payment_id)
 VALUES ($1,$2) ON CONFLICT DO NOTHING`, row, payment)
	return err
}

func (t *tasksDBDTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *tasksDBDTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *tasksDBDTransaction) Rollback() error { return t.ownedTransaction.Rollback() }

// --- django_redis cache lock --------------------------------------------------------

// tasksDBDRenewalLockKey is cache key 'renew_memberships_payments_lock' with
// django_redis' default prefix and version (":1:"), in the cache database.
const tasksDBDRenewalLockKey = ":1:renew_memberships_payments_lock"

// tasksDBDLockedPickle is django_redis' pickle (protocol 4) of "LOCKED".
var tasksDBDLockedPickle = []byte("\x80\x04\x95\x0a\x00\x00\x00\x00\x00\x00\x00\x8c\x06LOCKED\x94.")

// TasksDBDRenewalLock is cache.add(lock_id, "LOCKED", timeout=300) and
// cache.delete(lock_id) on Django's cache (Redis DB 1).
type TasksDBDRenewalLock struct{ cache *redis.Client }

func NewTasksDBDRenewalLock(cache *redis.Client) *TasksDBDRenewalLock {
	return &TasksDBDRenewalLock{cache: cache}
}

func (l *TasksDBDRenewalLock) Acquire(ctx context.Context) (bool, error) {
	return l.cache.SetNX(ctx, tasksDBDRenewalLockKey, tasksDBDLockedPickle, 300*time.Second).Result()
}

func (l *TasksDBDRenewalLock) Release(ctx context.Context) error {
	return l.cache.Del(ctx, tasksDBDRenewalLockKey).Err()
}
