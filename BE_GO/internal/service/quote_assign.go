package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/google/uuid"
)

// QuoteAssign is association_quote_assign() as Beat publishes it (no
// arguments): for every live association with multiple fees and an advanced
// plan, the plans starting today (UTC) assign a quota payment to each
// balance-year subscriber of the previous plan that has none, and every
// assigned member other than the owner is sent email/payment/fee_assigned.html
// through send_email_html. The task's outer except ends the run at the first
// exception, keeping the payments created before it.
type QuoteAssign struct {
	store      domain.QuoteAssignStore
	emails     *EmailTasks
	writes     *PaymentWrites
	whitelabel string
	now        func() time.Time
	logger     *slog.Logger
}

func NewQuoteAssign(store domain.QuoteAssignStore, emails *EmailTasks, whitelabel string, now func() time.Time, logger *slog.Logger) *QuoteAssign {
	return &QuoteAssign{store: store, emails: emails, writes: &PaymentWrites{now: now, newID: uuid.NewString}, whitelabel: whitelabel,
		now: now, logger: logger}
}

// quoteAssignAssociation is one association of the run: its balance year and
// active plans, or stop when Django raises before its plan loop.
type quoteAssignAssociation struct {
	association domain.QuoteAssignAssociation
	from, to    time.Time
	plans       []paymentQuotePlan
	stop        bool
}

func (s *QuoteAssign) Run(ctx context.Context, job domain.Job) error {
	if err := emailTaskEnvelope(job, domain.JobAssociationQuoteAssign); err != nil {
		return err
	}
	now := s.now().UTC()
	associations, err := s.plan(ctx, job, now)
	if err != nil {
		return err
	}
	err = nil
	for _, item := range associations {
		if item.stop {
			err = errPaymentMiscStop
			break
		}
		if err = s.association(ctx, job, item); err != nil {
			break
		}
	}
	if err != nil && !errors.Is(err, errPaymentMiscStop) {
		return err
	}
	if err != nil {
		s.logger.WarnContext(ctx, "association quote assign stopped", "code", "association_quote_assign_stopped")
	}
	tx, err := s.store.BeginQuoteAssign(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return tx.Commit()
}

// plan reads the associations (the iterator's cursor) and, for each one the
// run reaches, its balance year and active plans; it then checks every
// conversion, render and SMTP message the run could need and refuses the run
// (ErrPaymentMiscNotPorted, ErrEmailTaskUnsupported) before any effect.
func (s *QuoteAssign) plan(ctx context.Context, job domain.Job, now time.Time) ([]quoteAssignAssociation, error) {
	tx, err := s.store.BeginQuoteAssign(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QuoteAssignAssociations(ctx)
	if err != nil {
		return nil, err
	}
	out := []quoteAssignAssociation{}
	for _, row := range rows {
		item := quoteAssignAssociation{association: row}
		if item.from, item.to, err = paymentMiscBalanceYear(now, row.StartDay, row.StartMonth); err != nil {
			item.stop = true
			out = append(out, item)
			break
		}
		item.plans, err = paymentMiscActivePlans(row.Plans, now.Day(), int(now.Month()))
		if errors.Is(err, errPaymentMiscStop) {
			item.stop = true
			out = append(out, item)
			break
		}
		if err != nil {
			return nil, err
		}
		association, err := uuid.Parse(row.ID)
		if err != nil {
			return nil, err
		}
		for _, plan := range item.plans {
			pattern, err := paymentMiscPattern(plan.previous)
			if err != nil {
				return nil, err
			}
			if _, err = paymentMiscPattern(plan.id); err != nil {
				return nil, err
			}
			if _, _, _, err = paymentMiscAmount(plan.fee); err != nil {
				return nil, err
			}
			subscriptions, err := tx.QuoteAssignSubscriptions(ctx, row.ID, item.from, item.to, pattern)
			if err != nil {
				return nil, err
			}
			for _, subscription := range subscriptions {
				if subscription.UserID == row.OwnerID || subscription.AssociateID == nil {
					continue
				}
				if _, err = s.message(ctx, job, row, plan, subscription); err != nil {
					return nil, err
				}
				email := subscription.UserEmail
				if err = s.emails.CheckEmailHTML(ctx, job, association, s.subject(row, plan), []*string{&email}, []string{row.OwnerEmail}); err != nil {
					return nil, err
				}
			}
		}
		out = append(out, item)
	}
	return out, tx.Rollback()
}

// association runs one association's plan loop; errPaymentMiscStop is a
// Python exception that ends the task.
func (s *QuoteAssign) association(ctx context.Context, job domain.Job, item quoteAssignAssociation) error {
	row := item.association
	association, err := uuid.Parse(row.ID)
	if err != nil {
		return err
	}
	for _, plan := range item.plans {
		pattern, err := paymentMiscPattern(plan.previous)
		if err != nil {
			return err
		}
		subscriptions, err := s.subscriptions(ctx, job, row.ID, item.from, item.to, pattern)
		if err != nil {
			return err
		}
		for _, subscription := range subscriptions {
			created, err := s.quote(ctx, job, row, plan, subscription)
			if err != nil {
				return err
			}
			if !created {
				continue // an existing quote
			}
			if subscription.UserID == row.OwnerID {
				continue // no e-mail to the association
			}
			if subscription.AssociateID == nil {
				return errPaymentMiscStop // subscription.associate.first_name on None
			}
			message, err := s.message(ctx, job, row, plan, subscription)
			if err != nil {
				return err
			}
			email := subscription.UserEmail
			if err = s.emails.SendEmailHTML(ctx, job, association, s.subject(row, plan), message, []*string{&email},
				[]string{row.OwnerEmail}); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *QuoteAssign) subscriptions(ctx context.Context, job domain.Job, association string, from, to time.Time, pattern string) (
	[]domain.QuoteAssignSubscription, error) {
	tx, err := s.store.BeginQuoteAssign(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QuoteAssignSubscriptions(ctx, association, from, to, pattern)
	if err != nil {
		return nil, err
	}
	return rows, tx.Rollback()
}

// quote is the existing-payment check and Payment.objects.create (the
// association's default bank account, Payment.save() defaults, the audited
// create indexed directly), committed on its own. False: an existing quote.
func (s *QuoteAssign) quote(ctx context.Context, job domain.Job, row domain.QuoteAssignAssociation, plan paymentQuotePlan,
	subscription domain.QuoteAssignSubscription) (bool, error) {
	idPattern, err := paymentMiscPattern(plan.id)
	if err != nil {
		return false, err
	}
	stored, audit, float, err := paymentMiscAmount(plan.fee)
	if err != nil {
		return false, err
	}
	tx, err := s.store.BeginQuoteAssign(ctx, job)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	exists, err := tx.QuoteExists(ctx, domain.PaymentQuoteCheck{User: subscription.UserID, Associate: subscription.AssociateID,
		Pattern: idPattern, Amount: stored})
	if err != nil || exists {
		return false, err
	}
	account, err := tx.DefaultBankAccount(ctx, row.ID)
	if err != nil {
		return false, err
	}
	user, association := subscription.UserID, row.ID
	m := &paymentMemory{row: domain.PaymentWriteRow{ID: s.writes.newID(), Type: "default", Subject: 1, CreationDate: s.writes.at(),
		UserID: &user, AssociateID: subscription.AssociateID, AssociationID: &association, AccountID: account}, jsonAudit: map[string]string{}}
	if float != nil {
		m.setFloat(*float)
	} else {
		m.row.Amount, m.amountAudit, m.amountShown = stored, &audit, &stored
	}
	m.setJSON("meta", paymentQuoteMeta(plan), &m.row.Meta)
	if err = s.writes.defaults(ctx, tx, m); err != nil {
		return false, err
	}
	if err = tx.InsertPaymentRow(ctx, m.row); err != nil {
		return false, err
	}
	values, err := s.writes.values(ctx, tx, m)
	if err != nil {
		return false, err
	}
	// No actor in the worker; the index resolves the payment's own association.
	if err = tx.AppendPaymentWritesAudit(ctx, domain.PaymentWritesAuditEntry{PaymentID: m.row.ID, Action: 0,
		Changes: createdPaymentChanges(values), Association: &association, Path: "direct", At: s.writes.at()}); err != nil {
		return false, err
	}
	return true, tx.Commit()
}

// subject is f"{denomination} | Nuova quota associativa '{name}' assegnata".
func (s *QuoteAssign) subject(row domain.QuoteAssignAssociation, plan paymentQuotePlan) string {
	return paymentMiscText(row.Denomination) + " | Nuova quota associativa '" + dto.PythonStr(plan.name) + "' assegnata"
}

// message renders email/payment/fee_assigned.html with the plan's stored
// name and fee (JSON values the renderer does not take are not ported).
func (s *QuoteAssign) message(ctx context.Context, job domain.Job, row domain.QuoteAssignAssociation, plan paymentQuotePlan,
	subscription domain.QuoteAssignSubscription) (string, error) {
	name, err := quoteAssignTemplateValue(plan.name)
	if err != nil {
		return "", err
	}
	amount, err := quoteAssignTemplateValue(plan.fee)
	if err != nil {
		return "", err
	}
	var association any
	if row.Denomination != nil {
		association = *row.Denomination
	}
	return s.emails.RenderEmail(ctx, job, emailrender.FeeAssigned, map[string]any{
		"recipient_name": paymentMiscText(subscription.AssociateFirst) + " " + paymentMiscText(subscription.AssociateLast),
		"fee_name":       name, "amount": amount, "application_name": s.whitelabel, "association_name": association})
}

// quoteAssignTemplateValue is a json.loads value as a template context value.
func quoteAssignTemplateValue(value any) (any, error) {
	switch v := value.(type) {
	case nil, string, bool:
		return v, nil
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			if integer, err := strconv.ParseInt(string(v), 10, 64); err == nil {
				return integer, nil
			}
			return nil, domain.ErrPaymentMiscNotPorted
		}
		number, err := strconv.ParseFloat(string(v), 64)
		if err != nil {
			return nil, domain.ErrPaymentMiscNotPorted
		}
		return number, nil
	}
	return nil, domain.ErrPaymentMiscNotPorted
}
