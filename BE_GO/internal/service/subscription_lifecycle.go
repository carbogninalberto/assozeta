package service

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var (
	// ErrSubscriptionLifecycleForbidden is IsAssociationUser's generic 403 (archive).
	ErrSubscriptionLifecycleForbidden = errors.New("subscription lifecycle permission denied")
	// ErrSubscriptionLifecycleForeign is PermissionDenied('User not allowed.').
	ErrSubscriptionLifecycleForeign = errors.New("subscription lifecycle user not allowed")
	// ErrSubscriptionLifecycleRejected is reject's PermissionDenied on an already rejected row.
	ErrSubscriptionLifecycleRejected = errors.New("subscription already rejected")
)

// SubscriptionLifecycleLookupError is the UUIDField ValidationError of a
// lookup inside the archive/delete try block, rendered as a list.
type SubscriptionLifecycleLookupError struct{ Message string }

func (e *SubscriptionLifecycleLookupError) Error() string { return e.Message }

type SubscriptionLifecycles struct {
	store domain.SubscriptionLifecycleStore
	now   func() time.Time
}

func NewSubscriptionLifecycles(store domain.SubscriptionLifecycleStore, now func() time.Time) *SubscriptionLifecycles {
	return &SubscriptionLifecycles{store: store, now: now}
}

type subscriptionLifecycleRequest struct {
	tx          domain.SubscriptionLifecycleTransaction
	row         domain.SubscriptionLifecycleRow
	association string
	scope       *string
	group       *string
	audit       domain.CourseSubscriptionAudit
}

// open is the views' shared prologue after the uid check: the scoped
// association of request.user (a miss is a 500), then the scoped group-aware
// subscription; another association's row is PermissionDenied.
func (s *SubscriptionLifecycles) open(ctx context.Context, identity domain.ResolvedIdentity, id, groupHeader string,
	login domain.LoginAuditContext, parse func() (string, error)) (*subscriptionLifecycleRequest, error) {
	tx, err := s.store.BeginSubscriptionLifecycle(ctx)
	if err != nil {
		return nil, err
	}
	request := &subscriptionLifecycleRequest{tx: tx, scope: paymentCategoryAssociationScope(identity), group: supplierGroup(groupHeader)}
	fail := func(err error) (*subscriptionLifecycleRequest, error) {
		tx.Rollback()
		return nil, err
	}
	if request.association, err = tx.LifecycleAssociation(ctx, identity.ScopeUser.ID, request.scope); err != nil {
		return fail(err)
	}
	if parse != nil {
		if id, err = parse(); err != nil {
			return fail(err)
		}
	}
	row, err := tx.Subscription(ctx, id, request.scope, request.group)
	if err != nil {
		return fail(err)
	}
	if row == nil {
		return fail(domain.ErrSubscriptionLifecycleNotFound)
	}
	if row.AssociationID != request.association {
		return fail(ErrSubscriptionLifecycleForeign)
	}
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return fail(err)
	}
	request.row = *row
	request.audit = domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, login), ActorAssociation: actor,
		At: s.now().UTC().Truncate(time.Microsecond), Group: request.group}
	return request, nil
}

func lifecycleDate(value *time.Time) *domain.CalendarDate {
	if value == nil {
		return nil
	}
	return civilDate(*value)
}

func lifecycleDateText(value *domain.CalendarDate) *string {
	if value == nil {
		return nil
	}
	text := fmt.Sprintf("%04d-%02d-%02d", value.Year, value.Month, value.Day)
	return &text
}

// subscriptionSave is Subscription.save(): missing dates come from the
// owner's settings; changes compare against the stored row.
func subscriptionSave(ctx context.Context, row domain.SubscriptionLifecycleRow, status int, acceptance *domain.CalendarDate, archived bool,
	payment *string) (domain.SubscriptionLifecycleSave, error) {
	rule := domain.SubscriptionDateRule{Duration: row.Duration, Season: domain.CalendarRule{StartDay: row.StartDay, StartMonth: row.StartMonth,
		CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}}
	dates, err := ResolveSubscriptionDates(ctx, row.Created.UTC(), rule, domain.SubscriptionDates{Start: lifecycleDate(row.Start), End: lifecycleDate(row.End)})
	if err != nil {
		return domain.SubscriptionLifecycleSave{}, err
	}
	save := domain.SubscriptionLifecycleSave{ID: row.ID, AssociationID: row.AssociationID, Status: status, Acceptance: lifecycleDateText(acceptance),
		Start: lifecycleDateText(dates.Start), End: lifecycleDateText(dates.End), Archived: archived, PaymentID: payment, Changes: map[string][2]string{}}
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	compare := func(name string, old, new *string) {
		if text(old) != text(new) {
			save.Changes[name] = [2]string{text(old), text(new)}
		}
	}
	if status != row.Status {
		save.Changes["status_flag"] = [2]string{fmt.Sprint(row.Status), fmt.Sprint(status)}
	}
	compare("acceptance_date", lifecycleDateText(lifecycleDate(row.Acceptance)), save.Acceptance)
	compare("start_date", lifecycleDateText(lifecycleDate(row.Start)), save.Start)
	compare("end_date", lifecycleDateText(lifecycleDate(row.End)), save.End)
	if archived != row.Archived {
		save.Changes["archived"] = [2]string{pythonBool(row.Archived), pythonBool(archived)}
	}
	compare("payment", row.PaymentID, payment)
	return save, nil
}

// Reject follows subscription_reject.
func (s *SubscriptionLifecycles) Reject(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, login domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	request, err := s.open(ctx, identity, id, groupHeader, login, nil)
	if err != nil {
		return err
	}
	defer request.tx.Rollback()
	if request.row.Status == 3 {
		return ErrSubscriptionLifecycleRejected
	}
	save, err := subscriptionSave(ctx, request.row, 3, nil, request.row.Archived, request.row.PaymentID)
	if err != nil {
		return err
	}
	if err = request.tx.SaveSubscription(ctx, save, request.audit); err != nil {
		return err
	}
	return request.tx.Commit()
}

// lookupUUID is a UUIDField filter value inside the views' try block.
func lookupUUID(raw string) func() (string, error) {
	return func() (string, error) {
		id, err := paymentInfoParseUUID(raw)
		if err != nil {
			return "", &SubscriptionLifecycleLookupError{Message: "“" + raw + "” is not a valid UUID."}
		}
		return id.String(), nil
	}
}

func lifecycleMidnight(value *domain.CalendarDate) time.Time {
	return time.Date(value.Year, time.Month(value.Month), value.Day, 0, 0, 0, 0, time.UTC)
}

// hiddenPayment: the payment is not visible to Payment.objects (live,
// group-aware), whose auditlog diff and index the port does not reproduce.
func hiddenPayment(payment *domain.SubscriptionLifecyclePayment, group *string) bool {
	return payment.Deleted || (group != nil && (payment.GroupID == nil || *payment.GroupID != *group))
}

// archivable checks, before any write, the payments archive saves: an
// unpaid payment with an invoice (the invoice delete is not ported), one
// hidden from the default manager, one without an association or whose
// Payment.save() fails is refused.
func (s *SubscriptionLifecycles) archivable(ctx context.Context, request *subscriptionLifecycleRequest, id *string) error {
	if id == nil {
		return nil
	}
	payment, err := request.tx.Payment(ctx, *id)
	if err != nil {
		return err
	}
	if payment == nil {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	if payment.Paid {
		return nil
	}
	if payment.InvoiceID != nil || hiddenPayment(payment, request.group) || payment.AssociationID == "" {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	row := payment.PaymentSaveRow
	if err = paymentSaveDefaults(ctx, request.tx.PaymentSaves(), &row, map[string][2]string{}); err != nil {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	return nil
}

// archivePayment is payment.archived = True; payment.save() for an unpaid payment.
func (s *SubscriptionLifecycles) archivePayment(ctx context.Context, request *subscriptionLifecycleRequest, id *string) error {
	if id == nil {
		return nil
	}
	payment, err := request.tx.Payment(ctx, *id)
	if err != nil {
		return err
	}
	if payment == nil {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	if payment.Paid {
		return nil
	}
	return s.savePaymentArchived(ctx, request, payment.PaymentSaveRow)
}

func (s *SubscriptionLifecycles) savePaymentArchived(ctx context.Context, request *subscriptionLifecycleRequest, row domain.PaymentSaveRow) error {
	saves := request.tx.PaymentSaves()
	changes := map[string][2]string{}
	if !row.Archived {
		changes["archived"] = [2]string{"False", "True"}
	}
	if err := paymentSaveDefaults(ctx, saves, &row, changes); err != nil {
		return err
	}
	if err := saves.SavePaymentArchive(ctx, row.ID, true, row.CategoryID, row.Description); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	return saves.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		Context: request.audit.Context, At: request.audit.At})
}

// Archive follows subscription_archive (toggle; archiving cascades to the
// unpaid payments, installments and course subscriptions, then the
// unpaid payments of the subscription period).
func (s *SubscriptionLifecycles) Archive(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, login domain.LoginAuditContext) error {
	request, err := s.open(ctx, identity, raw, groupHeader, login, lookupUUID(raw))
	if err != nil {
		return err
	}
	tx := request.tx
	defer tx.Rollback()
	row := request.row
	if row.Archived {
		save, err := subscriptionSave(ctx, row, row.Status, lifecycleDate(row.Acceptance), false, row.PaymentID)
		if err != nil {
			return err
		}
		if err = tx.SaveSubscription(ctx, save, request.audit); err != nil {
			return err
		}
		return tx.Commit()
	}
	rows, err := tx.LiveCourseSubscriptions(ctx, row.ID, request.scope)
	if err != nil {
		return err
	}
	installments := make([][]domain.SubscriptionLifecycleInstallment, len(rows))
	for index, cs := range rows {
		if err = s.archivable(ctx, request, cs.PaymentID); err != nil {
			return err
		}
		if !cs.MultiPayments {
			continue
		}
		if installments[index], err = tx.LiveInstallments(ctx, cs.ID, request.group); err != nil {
			return err
		}
		for _, installment := range installments[index] {
			if err = s.archivable(ctx, request, installment.PaymentID); err != nil {
				return err
			}
		}
	}
	if err = s.archivable(ctx, request, row.PaymentID); err != nil {
		return err
	}
	save, err := subscriptionSave(ctx, row, row.Status, lifecycleDate(row.Acceptance), true, row.PaymentID)
	if err != nil {
		return err
	}
	if save.Start == nil || save.End == nil {
		// creation_date__range with a missing date is not ported.
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	for index, cs := range rows {
		if err = s.archivePayment(ctx, request, cs.PaymentID); err != nil {
			return err
		}
		for _, installment := range installments[index] {
			if err = s.archivePayment(ctx, request, installment.PaymentID); err != nil {
				return err
			}
			if err = tx.ArchiveInstallment(ctx, installment.ID, cs.CourseAssociationID, request.audit); err != nil {
				return err
			}
		}
		saved := domain.CourseSubscriptionSave{Row: cs}
		saved.Row.Archived = true
		saved.Representation = dto.CourseSubscriptionRepr(saved.Row, dto.CourseSubscriptionMemory{})
		if !cs.Archived {
			saved.Changes = map[string][2]string{"archived": {"False", "True"}}
		}
		saved.AssociationID, saved.ResolutionPath = cs.CourseAssociationID, "course.sport_association"
		if err = tx.SaveCourseSubscription(ctx, saved, request.audit); err != nil {
			return err
		}
	}
	if err = s.archivePayment(ctx, request, row.PaymentID); err != nil {
		return err
	}
	if err = tx.SaveSubscription(ctx, save, request.audit); err != nil {
		return err
	}
	payments, err := tx.RangePayments(ctx, request.association, row.AssociateID, lifecycleMidnight(lifecycleDateParse(save.Start)),
		lifecycleMidnight(lifecycleDateParse(save.End)), request.group)
	if err != nil {
		return err
	}
	for _, id := range payments {
		payment, err := tx.Payment(ctx, id)
		if err != nil {
			return err
		}
		if payment == nil || payment.AssociationID == "" {
			return domain.ErrSubscriptionLifecycleNotPorted
		}
		if err = s.savePaymentArchived(ctx, request, payment.PaymentSaveRow); err != nil {
			if errors.Is(err, ErrPaymentSaveFailed) {
				return domain.ErrSubscriptionLifecycleNotPorted
			}
			return err
		}
	}
	return tx.Commit()
}

func lifecycleDateParse(text *string) *domain.CalendarDate {
	if text == nil {
		return nil
	}
	var value domain.CalendarDate
	if _, err := fmt.Sscanf(*text, "%04d-%02d-%02d", &value.Year, &value.Month, &value.Day); err != nil {
		return nil
	}
	return &value
}

// Delete follows SubscriptionService.delete_subscription.
func (s *SubscriptionLifecycles) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, login domain.LoginAuditContext) error {
	request, err := s.open(ctx, identity, raw, groupHeader, login, lookupUUID(raw))
	if err != nil {
		return err
	}
	tx := request.tx
	defer tx.Rollback()
	row := request.row
	// Checked before any write: the on-commit signature object cleanup, the
	// cascades to other models, invoices of deleted payments and unpaid
	// payments reached twice are not ported.
	if row.SignatureKey != nil && *row.SignatureKey != "" {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	referenced, err := tx.SubscriptionReferenced(ctx, row.ID)
	if err != nil {
		return err
	}
	if referenced {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	rows, err := tx.LiveCourseSubscriptions(ctx, row.ID, nil)
	if err != nil {
		return err
	}
	installments := make([][]domain.SubscriptionLifecycleInstallment, len(rows))
	unpaid := map[string]bool{}
	reached := func(id *string) (bool, error) {
		if id == nil {
			return false, nil
		}
		payment, err := tx.Payment(ctx, *id)
		if err != nil {
			return false, err
		}
		if payment == nil {
			return false, domain.ErrSubscriptionLifecycleNotPorted
		}
		if payment.Paid {
			return false, nil
		}
		if payment.InvoiceID != nil || unpaid[*id] {
			return false, domain.ErrSubscriptionLifecycleNotPorted
		}
		unpaid[*id] = true
		return true, nil
	}
	for index, cs := range rows {
		if _, err = reached(cs.PaymentID); err != nil {
			return err
		}
		if !cs.MultiPayments {
			continue
		}
		if installments[index], err = tx.LiveInstallments(ctx, cs.ID, request.group); err != nil {
			return err
		}
		for _, installment := range installments[index] {
			if _, err = reached(installment.PaymentID); err != nil {
				return err
			}
		}
	}
	subscriptionPayment, err := reached(row.PaymentID)
	if err != nil {
		return err
	}
	save, err := subscriptionSave(ctx, row, row.Status, lifecycleDate(row.Acceptance), true, nil)
	if err != nil {
		return err
	}
	if save.Start == nil || save.End == nil {
		return domain.ErrSubscriptionLifecycleNotPorted
	}

	audit := request.audit
	for index, cs := range rows {
		if cs.PaymentID != nil && unpaid[*cs.PaymentID] {
			if err = tx.DeletePayments(ctx, []string{*cs.PaymentID}, audit); err != nil {
				return err
			}
			cs.PaymentID = nil
		}
		for _, installment := range installments[index] {
			if installment.PaymentID != nil && unpaid[*installment.PaymentID] {
				if err = tx.DeleteLoopInstallment(ctx, installment, audit); err != nil {
					return err
				}
				continue
			}
			if err = tx.ArchiveInstallment(ctx, installment.ID, cs.CourseAssociationID, audit); err != nil {
				return err
			}
		}
		// CourseSubscription.delete(): the live installments, then deleted=True.
		if err = tx.DeleteInstallmentsOf(ctx, cs.ID, audit); err != nil {
			return err
		}
		saved := domain.CourseSubscriptionSave{Row: cs}
		saved.Row.Deleted = true
		saved.Representation = dto.CourseSubscriptionRepr(saved.Row, dto.CourseSubscriptionMemory{})
		saved.Changes = map[string][2]string{"deleted": {"False", "True"}}
		saved.AssociationID, saved.ResolutionPath = cs.CourseAssociationID, "course.sport_association"
		if err = tx.SaveCourseSubscription(ctx, saved, audit); err != nil {
			return err
		}
	}
	payment := row.PaymentID
	if subscriptionPayment {
		if err = tx.DeletePayments(ctx, []string{*row.PaymentID}, audit); err != nil {
			return err
		}
		payment = nil
	}
	if save, err = subscriptionSave(ctx, row, row.Status, lifecycleDate(row.Acceptance), true, payment); err != nil {
		return err
	}
	if subscriptionPayment {
		// The SET_NULL already cleared the stored reference.
		delete(save.Changes, "payment")
	}
	if err = tx.SaveSubscription(ctx, save, audit); err != nil {
		return err
	}
	payments, err := tx.RangePayments(ctx, request.association, row.AssociateID, lifecycleMidnight(lifecycleDateParse(save.Start)),
		lifecycleMidnight(lifecycleDateParse(save.End)), request.group)
	if err != nil {
		return err
	}
	if err = tx.DeletePayments(ctx, payments, audit); err != nil {
		return err
	}
	if err = tx.DeleteSubscription(ctx, row.ID, audit); err != nil {
		return err
	}
	return tx.Commit()
}
