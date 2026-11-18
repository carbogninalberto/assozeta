package service

import (
	"context"
	"errors"
	"log/slog"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
)

// StripePaidCheck is mark_as_paid_payments_checking_stripe: without direct
// Stripe credentials nothing happens; otherwise every unpaid live payment
// with an intent goes through _check_and_mark_payment in one transaction:
// the payment is locked, an unpaid one is checked on Stripe, and a
// succeeded intent sets type 'stripe' (update_fields) and runs
// mark_payment_as_paid with a request whose user is payment.user (no
// Authorization header). A StripeError, a falsy intent or another status
// leaves the payment; any other exception rolls the block back and the
// loop goes on.
//
// Django submits the payments to a ten-thread pool and invoice numbers
// follow the threads' completion order (two blocks can even read the same
// last number), so its outcome is not deterministic. Go processes the
// payments one at a time in payment_id order; that is the outcome of a
// Django run on a pool of one thread over the same order.
type StripePaidCheck struct {
	store  domain.StripePaidCheckStore
	stripe *StripeRoutes
	logger *slog.Logger
}

func NewStripePaidCheck(store domain.StripePaidCheckStore, stripe *StripeRoutes, logger *slog.Logger) *StripePaidCheck {
	return &StripePaidCheck{store: store, stripe: stripe, logger: logger}
}

// errStripePaidCheckException is an exception _check_and_mark_payment
// re-raises: the block is rolled back and the loop goes on.
var errStripePaidCheckException = errors.New("stripe paid check: python exception")

// stripePaidCheckJSONEdges are the paid check's edges for the stored payment
// JSON its mark flow loads. The Celery worker's stack is not measured: owner
// decision X15 answers with the band measured for the HTTP site that runs the
// same mark_payment_as_paid (the Stripe webhook).
var stripePaidCheckJSONEdges = stripeWebhookJSONEdges

// stripePaidCheckPython reports the domain errors that stand for a Python
// exception inside the paid flow. A stored payment past the mark's load edge is
// a RecursionError inside the thread-pool future: it is a counted failure and
// the loop goes on.
func stripePaidCheckPython(err error) bool {
	for _, target := range []error{domain.ErrStripeServer, domain.ErrPaymentStateServer, domain.ErrInvoiceCoreServer, domain.ErrInvoicePartyServer,
		domain.ErrInvoicePartyMissing, domain.ErrPaymentDescription, domain.ErrPaymentSaveScope, ErrPaymentSaveFailed,
		errStripePaidCheckException, errPaymentStoredJSONRecursion} {
		if errors.Is(err, target) {
			return true
		}
	}
	return false
}

// stripePaidCheckDiscard is the publisher of the planning pass: nothing is sent.
type stripePaidCheckDiscard struct{}

func (stripePaidCheckDiscard) Publish(context.Context, celery.Task) (string, error) { return "", nil }

func (s *StripePaidCheck) Run(ctx context.Context, job domain.Job) error {
	if err := tasksDBDEnvelope(job, domain.JobMarkAsPaidPaymentsCheckingStripe); err != nil {
		return err
	}
	configured, err := s.stripe.TasksDBDCredentialsConfigured(ctx)
	if err != nil {
		return err
	}
	if configured {
		ids, err := s.plan(ctx, job)
		if err != nil {
			return err
		}
		var session *stripeapi.Session
		for _, id := range ids {
			if err = s.check(ctx, job, id, &session); err != nil {
				return err
			}
		}
	}
	tx, err := s.store.BeginStripePaidCheck(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return tx.Commit()
}

// plan lists the payments and runs each one's mark flow as if its intent had
// succeeded, in a transaction that is rolled back and with a publisher that
// sends nothing: a path Go does not port refuses the run before any effect.
func (s *StripePaidCheck) plan(ctx context.Context, job domain.Job) ([]string, error) {
	tx, err := s.store.BeginStripePaidCheck(ctx, job)
	if err != nil {
		return nil, err
	}
	ids, err := tx.StripePaidCheckPayments(ctx)
	if err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	// Django's list() loads every candidate before the pool starts: a
	// RecursionError there ends the task with nothing written. The candidates
	// are all locked in this planning transaction first, so the order of ids
	// does not decide whether the run stops.
	for _, id := range ids {
		row, err := tx.LockStripePaidCheck(ctx, id)
		if err != nil {
			_ = tx.Rollback()
			return nil, err
		}
		if row != nil && paymentJSONTextsDepth(row.MetaPaymentCategories, row.Meta, row.Course) > stripePaidCheckJSONEdges.Load {
			_ = tx.Rollback()
			s.logger.WarnContext(ctx, "stripe paid check stopped", "code", "stripe_paid_check_json_recursion")
			return nil, nil
		}
	}
	_ = tx.Rollback()
	dry := *s.stripe
	dry.publisher = stripePaidCheckDiscard{}
	for _, id := range ids {
		tx, err := s.store.BeginStripePaidCheck(ctx, job)
		if err != nil {
			return nil, err
		}
		row, err := tx.LockStripePaidCheck(ctx, id)
		if err == nil && row != nil && !row.Paid {
			_, err = s.mark(ctx, tx, &dry, *row)
		}
		_ = tx.Rollback()
		if err != nil && errors.Is(err, refusal.ErrNotPorted) {
			s.logger.WarnContext(ctx, "stripe paid check refused", "code", "stripe_paid_check_not_ported")
			return nil, err
		}
		if err != nil && !stripePaidCheckPython(err) {
			return nil, err
		}
	}
	return ids, nil
}

// check is _check_and_mark_payment for one payment.
func (s *StripePaidCheck) check(ctx context.Context, job domain.Job, id string, session **stripeapi.Session) error {
	tx, err := s.store.BeginStripePaidCheck(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := tx.LockStripePaidCheck(ctx, id)
	if err != nil {
		return err
	}
	if row == nil {
		// Payment.DoesNotExist: re-raised and counted, the loop goes on.
		return nil
	}
	if row.Paid {
		return nil
	}
	intentID := ""
	if row.IntentID != nil {
		intentID = *row.IntentID
	}
	if intentID == "" {
		// PaymentIntent.retrieve('') raises InvalidRequestError before any request.
		return nil
	}
	key, err := s.stripe.TasksDBDRequestKey(ctx)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		// stripe_request_options() raises: re-raised and counted.
		return nil
	}
	if *session == nil {
		*session = s.stripe.client.Session(key)
	}
	intent, err := (*session).RetrievePaymentIntent(ctx, intentID)
	if err != nil {
		handled, err := tasksDBDStripeError(ctx, err)
		if handled {
			return nil
		}
		return err
	}
	if len(intent) == 0 {
		return nil // an empty StripeObject is falsy
	}
	status, present := intent.Lookup("status")
	if !present {
		return nil // AttributeError: re-raised, the block is rolled back
	}
	if status != "succeeded" {
		return nil
	}
	notification, err := s.mark(ctx, tx, s.stripe, *row)
	if stripePaidCheckPython(err) {
		s.logger.WarnContext(ctx, "stripe paid check payment failed", "code", "stripe_paid_check_exception")
		return nil
	}
	if err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return notification.deliver(ctx, s.stripe.notifier)
}

// mark is the succeeded branch inside the block: the type save, then
// mark_payment_as_paid(request, payment): the first supplier invoice marked
// paid, the payment saved paid (stripeMarkPaid), the invoice flow for a
// payment without invoice, and payment.user.user_id read for the log (no
// user: an exception that rolls the block back, discarding the on-commit
// print_document_invoice).
func (s *StripePaidCheck) mark(ctx context.Context, tx domain.StripePaidCheckTransaction, stripe *StripeRoutes,
	row domain.StripePaidCheckPayment) (*paymentPaidFlowNotification, error) {
	// The job runs on a Celery worker whose stack has not been measured: X15
	// answers with the band measured for the webhook, the HTTP site that runs
	// the same mark_payment_as_paid (owner decision).
	ctx = withPaymentJSONEdges(ctx, stripePaidCheckJSONEdges)
	now := stripe.at()
	if row.Type != "stripe" {
		if err := tx.SaveStripePaidCheckType(ctx, row, now); err != nil {
			return nil, err
		}
	}
	invoice, err := tx.SupplierInvoice(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	if invoice != nil {
		changes := map[string][2]string{}
		if !invoice.Paid {
			changes["paid"] = [2]string{"False", "True"}
		}
		if before, after := courseSubscriptionAuditTime(invoice.PaymentDate), courseSubscriptionAuditTime(&now); before != after {
			changes["payment_date"] = [2]string{before, after}
		}
		if err := tx.SaveSupplierInvoice(ctx, invoice.ID, true, &now); err != nil {
			return nil, err
		}
		if err := tx.AppendSupplierInvoiceAudit(ctx, *invoice, changes, domain.LoginAuditContext{}, now); err != nil {
			return nil, err
		}
	}
	locked, err := tx.LockStripePayment(ctx, row.ID, nil)
	if err != nil {
		return nil, err
	}
	if locked == nil {
		return nil, errStripePaidCheckException
	}
	if err = stripeMarkPaid(ctx, tx, &locked.PaymentSaveRow, locked.PaymentDate, domain.LoginAuditContext{}, now); err != nil {
		return nil, err
	}
	var notification *paymentPaidFlowNotification
	if row.InvoiceID == nil {
		if notification, err = stripe.stripePaidFlow(ctx, tx, domain.ResolvedIdentity{}, StripeRequest{}, row.ID, now, true,
			&locked.PaymentSaveRow); err != nil {
			return nil, err
		}
	}
	if row.UserID == nil {
		return nil, errStripePaidCheckException
	}
	return notification, nil
}
