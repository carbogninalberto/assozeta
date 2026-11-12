package service

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"log/slog"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
	"github.com/google/uuid"
)

// TasksDBDStripe is the Stripe configuration the tasks read per call:
// stripe_direct_credentials_configured() and stripe_request_options()['api_key'].
type TasksDBDStripe interface {
	TasksDBDCredentialsConfigured(context.Context) (bool, error)
	TasksDBDRequestKey(context.Context) (string, error)
}

// TasksDBDCredentialsConfigured is stripe_direct_credentials_configured().
func (s *StripeRoutes) TasksDBDCredentialsConfigured(ctx context.Context) (bool, error) {
	return s.availability.DirectCredentialsConfigured(ctx)
}

// TasksDBDRequestKey is stripe_request_options()['api_key'] (an empty or
// undecryptable key raises).
func (s *StripeRoutes) TasksDBDRequestKey(ctx context.Context) (string, error) {
	return s.requestKey(ctx)
}

// TasksDBD implements change_type_of_stripe_payments,
// generate_coupon_if_not_exists and renew_memberships_payments
// (BE/application/tasks.py). Each catches its exceptions and returns None; Go
// records that handled outcome as a NULL completion with the earlier committed
// effects kept.
type TasksDBD struct {
	store     domain.TasksDBDStore
	stripe    TasksDBDStripe
	client    *stripeapi.Client
	publisher celery.Publisher
	lock      domain.RenewalCacheLock
	now       func() time.Time
	sleep     func(context.Context, time.Duration) error
	logger    *slog.Logger
}

func NewTasksDBD(store domain.TasksDBDStore, stripe TasksDBDStripe, client *stripeapi.Client, publisher celery.Publisher,
	lock domain.RenewalCacheLock, now func() time.Time, logger *slog.Logger) *TasksDBD {
	return &TasksDBD{store: store, stripe: stripe, client: client, publisher: publisher, lock: lock, now: now,
		sleep: tasksDBDSleep, logger: logger}
}

func tasksDBDSleep(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// errTasksDBDStopped is an exception the task's outer handler catches.
var errTasksDBDStopped = errors.New("task stopped by a handled exception")

func tasksDBDEnvelope(job domain.Job, kind string) error {
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != kind ||
		job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	return nil
}

// finish completes the job with NULL after a normal end or a handled stop.
func (s *TasksDBD) finish(ctx context.Context, job domain.Job, err error, name string) error {
	if err != nil && !errors.Is(err, errTasksDBDStopped) {
		return err
	}
	if err != nil {
		s.logger.WarnContext(ctx, name+" stopped", "code", "task_stopped")
	}
	tx, err := s.store.BeginTasksDBD(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return tx.Commit()
}

// stripeFailure maps an SDK call error: a stripe.error.StripeError is
// returned as handled=true; cancellation and unported values go back.
func tasksDBDStripeError(ctx context.Context, err error) (bool, error) {
	if ctx.Err() != nil {
		return false, ctx.Err()
	}
	var failure *stripeapi.Error
	if errors.As(err, &failure) {
		if failure.Status >= 200 && failure.Status < 300 && failure.Message == "response is not an object" {
			// A JSON success that is not an object is not characterized.
			return false, errors.New("stripe response is not an object")
		}
		return true, nil
	}
	return false, err
}

// --- change_type_of_stripe_payments --------------------------------------------

const (
	tasksDBDBatchSize = 100
	tasksDBDDelay     = time.Second
)

// RunChangeTypeOfStripePayments: without direct credentials nothing happens;
// otherwise the count is read once and each slice of 100 (re-queried, no
// ordering) is checked payment by payment, 1 s apart; succeeded intents are
// bulk-updated to type 'stripe' per batch. A StripeError is swallowed per
// payment; any other exception ends the task, losing the batch's pending update.
func (s *TasksDBD) RunChangeTypeOfStripePayments(ctx context.Context, job domain.Job) error {
	if err := tasksDBDEnvelope(job, domain.JobChangeTypeOfStripePayments); err != nil {
		return err
	}
	return s.finish(ctx, job, s.changeType(ctx, job), "change_type_of_stripe_payments")
}

func (s *TasksDBD) read(ctx context.Context, job domain.Job, read func(domain.TasksDBDTransaction) error) error {
	tx, err := s.store.BeginTasksDBD(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = read(tx); err != nil {
		if ctx.Err() != nil || errors.Is(err, domain.ErrJobLeaseLost) {
			return err
		}
		return errors.Join(errTasksDBDStopped, err)
	}
	return tx.Commit()
}

func (s *TasksDBD) changeType(ctx context.Context, job domain.Job) error {
	configured, err := s.stripe.TasksDBDCredentialsConfigured(ctx)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errors.Join(errTasksDBDStopped, err)
	}
	if !configured {
		return nil
	}
	// datetime.now() - timedelta(days=10), naive UTC.
	cutoff := s.now().UTC().Truncate(time.Microsecond).Add(-10 * 24 * time.Hour)
	var total int
	if err = s.read(ctx, job, func(tx domain.TasksDBDTransaction) (err error) {
		total, err = tx.StripeTypeCount(ctx, cutoff)
		return err
	}); err != nil {
		return err
	}
	var session *stripeapi.Session
	for processed := 0; processed < total; processed += tasksDBDBatchSize {
		var batch []domain.StripeTypeCandidate
		if err = s.read(ctx, job, func(tx domain.TasksDBDTransaction) (err error) {
			batch, err = tx.StripeTypeBatch(ctx, cutoff, processed, tasksDBDBatchSize)
			return err
		}); err != nil {
			return err
		}
		var update []uuid.UUID
		for _, payment := range batch {
			key, err := s.stripe.TasksDBDRequestKey(ctx)
			if err != nil {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				return errors.Join(errTasksDBDStopped, err)
			}
			if session == nil {
				session = s.client.Session(key)
			}
			// PaymentIntent.retrieve('') raises InvalidRequestError before any request.
			if payment.IntentID != "" {
				intent, err := session.RetrievePaymentIntent(ctx, payment.IntentID)
				if err != nil {
					handled, err := tasksDBDStripeError(ctx, err)
					if !handled {
						return err
					}
				} else if len(intent) > 0 {
					// An empty StripeObject is falsy; a missing status is an AttributeError.
					status, present := intent.Lookup("status")
					if !present {
						return errTasksDBDStopped
					}
					if status == "succeeded" {
						update = append(update, payment.ID)
					}
				}
			}
			if err := s.sleep(ctx, tasksDBDDelay); err != nil {
				return err
			}
		}
		if len(update) > 0 {
			tx, err := s.store.BeginTasksDBD(ctx, job)
			if err != nil {
				return err
			}
			if err = tx.MarkStripeType(ctx, update); err != nil {
				_ = tx.Rollback()
				if ctx.Err() != nil {
					return ctx.Err()
				}
				return errors.Join(errTasksDBDStopped, err)
			}
			if err = tx.Commit(); err != nil {
				return err
			}
		}
	}
	return nil
}

// --- generate_coupon_if_not_exists ---------------------------------------------

// tasksDBDCodeAlphabet is generate_readable_unique_string's alphabet before .upper().
const tasksDBDCodeAlphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"

// tasksDBDReadableCode is generate_readable_unique_string(): eight
// secrets.choice draws from the alphabet, upper-cased.
func tasksDBDReadableCode() (string, error) {
	limit := big.NewInt(int64(len(tasksDBDCodeAlphabet)))
	code := make([]byte, 8)
	for i := range code {
		n, err := rand.Int(rand.Reader, limit)
		if err != nil {
			return "", err
		}
		code[i] = tasksDBDCodeAlphabet[n.Int64()]
	}
	return strings.ToUpper(string(code)), nil
}

// RunGenerateCouponIfNotExists: for each live association without an affiliate
// code, Coupon.create(percent_off=10.0, duration='once', name=denomination[:40]),
// PromotionCode.create(coupon=id, code=<readable>, active=True), then
// sport_association.save(). The first exception ends the task.
func (s *TasksDBD) RunGenerateCouponIfNotExists(ctx context.Context, job domain.Job) error {
	if err := tasksDBDEnvelope(job, domain.JobGenerateCouponIfNotExists); err != nil {
		return err
	}
	return s.finish(ctx, job, s.coupons(ctx, job), "generate_coupon_if_not_exists")
}

func (s *TasksDBD) coupons(ctx context.Context, job domain.Job) error {
	var associations []domain.CouponAssociation
	if err := s.read(ctx, job, func(tx domain.TasksDBDTransaction) (err error) {
		associations, err = tx.CouponAssociations(ctx)
		return err
	}); err != nil {
		return err
	}
	var session *stripeapi.Session
	call := func(path string, params []stripeapi.Param) (stripeapi.Object, error) {
		key, err := s.stripe.TasksDBDRequestKey(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, errors.Join(errTasksDBDStopped, err)
		}
		if session == nil {
			session = s.client.Session(key)
		}
		object, err := session.Create(ctx, path, params)
		if err != nil {
			if handled, err := tasksDBDStripeError(ctx, err); !handled {
				return nil, err
			}
			return nil, errTasksDBDStopped
		}
		return object, nil
	}
	for _, association := range associations {
		code, err := tasksDBDReadableCode()
		if err != nil {
			return err
		}
		name := []rune(association.Denomination)
		if len(name) > 40 {
			name = name[:40]
		}
		coupon, err := call("/v1/coupons", []stripeapi.Param{{Key: "percent_off", Value: stripeapi.Float(10.0)},
			{Key: "duration", Value: "once"}, {Key: "name", Value: string(name)}})
		if err != nil {
			return err
		}
		value, present := coupon.Lookup("id")
		if !present {
			return errTasksDBDStopped
		}
		id, err := tasksDBDCouponID(value)
		if err != nil {
			return err
		}
		if _, err = call("/v1/promotion_codes", []stripeapi.Param{{Key: "coupon", Value: value}, {Key: "code", Value: code},
			{Key: "active", Value: true}}); err != nil {
			return err
		}
		tx, err := s.store.BeginTasksDBD(ctx, job)
		if err != nil {
			return err
		}
		if err = tx.SaveCoupon(ctx, association, code, id, s.now().UTC()); err != nil {
			_ = tx.Rollback()
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return errors.Join(errTasksDBDStopped, err)
		}
		if err = tx.Commit(); err != nil {
			return err
		}
	}
	return nil
}

// tasksDBDCouponID is str(coupon_stripe.id) as the CharField stores it; only
// string and integer ids are ported.
func tasksDBDCouponID(value any) (string, error) {
	switch v := value.(type) {
	case string:
		return v, nil
	case json.Number:
		if _, err := v.Int64(); err == nil {
			return v.String(), nil
		}
	}
	return "", errors.New("coupon id value not ported")
}

// --- renew_memberships_payments ------------------------------------------------------

const (
	tasksDBDRenewAttempt     = 1
	tasksDBDRenewMaxAttempts = 3
)

// RunRenewMembershipsPayments is the scheduled call (attempt=1, max_attempts=3).
// The Django cache lock is taken first (held: nothing happens). A run whose
// due period already has a paid payment would end with Django's synchronous
// send_mail to support: it is refused (lock released, no effect). Each due
// subscription is then renewed in its own transaction; a locked row is skipped
// and, after the lock release, the retry is published as Celery's
// apply_async(kwargs={'attempt': 2, 'max_attempts': 3}, countdown=60).
func (s *TasksDBD) RunRenewMembershipsPayments(ctx context.Context, job domain.Job) error {
	if err := tasksDBDEnvelope(job, domain.JobRenewMembershipsPayments); err != nil {
		return err
	}
	added, err := s.lock.Acquire(ctx)
	if err != nil {
		return err
	}
	if !added {
		return s.finish(ctx, job, nil, "renew_memberships_payments")
	}
	lockErrors, paidSkips, err := s.renew(ctx, job)
	// finally: cache.delete(lock_id), even when the run is cancelled.
	release, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	releaseErr := s.lock.Release(release)
	cancel()
	if err != nil && !errors.Is(err, errTasksDBDStopped) {
		return err
	}
	if releaseErr != nil {
		return releaseErr
	}
	if lockErrors && tasksDBDRenewAttempt < tasksDBDRenewMaxAttempts {
		eta := s.now().UTC().Add(60 * time.Second)
		if _, err := s.publisher.Publish(ctx, celery.Task{Name: domain.JobRenewMembershipsPayments, ETA: &eta, Kwargs: celery.Dict{
			{Key: "attempt", Value: tasksDBDRenewAttempt + 1}, {Key: "max_attempts", Value: tasksDBDRenewMaxAttempts}}}); err != nil {
			return err
		}
		return s.finish(ctx, job, err, "renew_memberships_payments")
	}
	if paidSkips > 0 {
		// Only a concurrent payment can reach this after the planning check.
		return domain.ErrRenewalSupportEmail
	}
	return s.finish(ctx, job, err, "renew_memberships_payments")
}

// tasksDBDAddMonths is datetime + relativedelta(months=n): the day is clamped
// to the target month's length, the time of day is kept.
func tasksDBDAddMonths(at time.Time, months int) time.Time {
	total := at.Year()*12 + int(at.Month()) - 1 + months
	year, month := total/12, time.Month(total%12+1)
	last := time.Date(year, month+1, 0, 0, 0, 0, 0, time.UTC).Day()
	day := at.Day()
	if day > last {
		day = last
	}
	return time.Date(year, month, day, at.Hour(), at.Minute(), at.Second(), at.Nanosecond(), time.UTC)
}

func tasksDBDDate(at time.Time) string { return at.Format("2006-01-02") }

// tasksDBDIsoformat is datetime.isoformat() of an aware UTC datetime.
func tasksDBDIsoformat(at time.Time) string {
	if at.Nanosecond()/1000 == 0 {
		return at.Format("2006-01-02T15:04:05") + "+00:00"
	}
	return at.Format("2006-01-02T15:04:05.000000") + "+00:00"
}

func (s *TasksDBD) renew(ctx context.Context, job domain.Job) (lockErrors bool, paidSkips int, err error) {
	now := s.now().UTC().Truncate(time.Microsecond)
	var candidates []domain.RenewalCandidate
	refused := false
	if err = s.read(ctx, job, func(tx domain.TasksDBDTransaction) error {
		var err error
		if candidates, err = tx.RenewalCandidates(ctx, now); err != nil {
			return err
		}
		// Planning: a paid payment for a candidate's next period leads to
		// Django's support e-mail; strftime outside four-digit years is not ported.
		for _, candidate := range candidates {
			until := tasksDBDAddMonths(candidate.BilledUntil.UTC(), candidate.BilledFrequency)
			if candidate.BilledUntil.UTC().Year() < 1000 || until.Year() > 9999 {
				refused = true
				return nil
			}
			if candidate.SubscriptionID == nil {
				continue
			}
			existing, err := tx.RenewalExisting(ctx, candidate.ID, tasksDBDDate(candidate.BilledUntil.UTC()), tasksDBDDate(until))
			if err != nil {
				return err
			}
			if existing != nil && existing.Paid {
				refused = true
				return nil
			}
		}
		return nil
	}); err != nil {
		if errors.Is(err, errTasksDBDStopped) {
			return false, 0, err
		}
		return false, 0, err
	}
	if refused {
		return false, 0, domain.ErrRenewalSupportEmail
	}
	for _, candidate := range candidates {
		if candidate.SubscriptionID == nil {
			continue
		}
		locked, paid, err := s.renewOne(ctx, job, candidate)
		if err != nil {
			return lockErrors, paidSkips, err
		}
		lockErrors = lockErrors || locked
		if paid {
			paidSkips++
		}
	}
	return lockErrors, paidSkips, nil
}

// renewOne is one `with transaction.atomic()` block; an exception inside it
// rolls the block back and the loop continues.
func (s *TasksDBD) renewOne(ctx context.Context, job domain.Job, candidate domain.RenewalCandidate) (locked, paid bool, err error) {
	tx, err := s.store.BeginTasksDBD(ctx, job)
	if err != nil {
		return false, false, err
	}
	defer tx.Rollback()
	skip := func(err error) (bool, bool, error) {
		if ctx.Err() != nil {
			return false, false, ctx.Err()
		}
		if errors.Is(err, domain.ErrJobLeaseLost) {
			return false, false, err
		}
		s.logger.WarnContext(ctx, "membership renewal failed", "code", "renewal_failed")
		return false, false, nil
	}
	row, err := tx.LockRenewal(ctx, candidate.ID)
	if errors.Is(err, domain.ErrRenewalLocked) || errors.Is(err, domain.ErrRenewalMissing) {
		return true, false, nil
	}
	if err != nil {
		return skip(err)
	}
	from := row.BilledUntil.UTC()
	until := tasksDBDAddMonths(from, row.BilledFrequency)
	existing, err := tx.RenewalExisting(ctx, row.ID, tasksDBDDate(from), tasksDBDDate(until))
	if err != nil {
		return skip(err)
	}
	var payment *uuid.UUID
	switch {
	case existing != nil && existing.Paid:
		paid = true
	case existing != nil:
		payment = &existing.ID
	default:
		relations, err := tx.RenewalRelations(ctx, row)
		if err != nil {
			return skip(err)
		}
		if relations.AssociationID == nil || !relations.HasSubject || row.MembershipFee == nil {
			return skip(domain.ErrRenewalAttribute)
		}
		fee := *row.MembershipFee
		meta := domain.RequestObject{{Name: "course_id", Value: row.CourseID.String()},
			{Name: "course_subscription_id", Value: row.ID.String()}, {Name: "billed_from", Value: tasksDBDDate(from)},
			{Name: "billed_until", Value: tasksDBDDate(until)}, {Name: "amount", Value: fee},
			{Name: "renewal_timestamp", Value: tasksDBDIsoformat(s.now().UTC())}}
		created := domain.RenewalPayment{ID: uuid.New(), CourseSub: row.ID, Amount: fee, BilledUntil: from,
			Description: "Abbonamento: " + relations.CourseTitle + " dal " + from.Format("02/01/2006") + " al " + until.Format("02/01/2006"),
			Meta:        string(dto.PythonJSONDumps(meta)), OwnerID: relations.OwnerID, AssociateID: relations.AssociateID,
			AssociationID: *relations.AssociationID}
		if err = tx.CreateRenewalPayment(ctx, created, s.now().UTC()); err != nil {
			return skip(err)
		}
		payment = &created.ID
	}
	if err = tx.AdvanceRenewal(ctx, row.ID, until); err != nil {
		return skip(err)
	}
	if payment != nil {
		if err = tx.AddMembershipPayment(ctx, row.ID, *payment); err != nil {
			return skip(err)
		}
	}
	return false, paid, tx.Commit()
}
