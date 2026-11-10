package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// PaymentStates ports payment_approve, payment_cancel, payment_delete,
// payment_bulk_archive and payment_bulk_delete.
type PaymentStates struct {
	store domain.PaymentStateStore
	now   func() time.Time
	// tasks publishes approve's print_document_invoice; nil refuses that path.
	tasks celery.Publisher
	log   *slog.Logger
	// paid and notifier run approve's paid flow (invoice, notification); nil refuses it.
	paid     domain.PaymentPaidFlowStore
	notifier *SubscriptionNotifier
	// printer runs delete's eager print_document_invoice; nil refuses deleting an invoiced payment.
	printer *InvoiceSyncPrint
}

func NewPaymentStates(store domain.PaymentStateStore, now func() time.Time) *PaymentStates {
	return &PaymentStates{store: store, now: now, log: slog.Default()}
}

// WithTasks lets approve dispatch the invoice PDF task through the Celery producer.
func (s *PaymentStates) WithTasks(tasks celery.Publisher) *PaymentStates {
	s.tasks = tasks
	return s
}

// WithInvoicePrint lets delete print an invoiced payment's invoice synchronously (.apply).
func (s *PaymentStates) WithInvoicePrint(printer *InvoiceSyncPrint) *PaymentStates {
	s.printer = printer
	return s
}

// WithPaidFlow lets approve mark unpaid income payments with a party paid
// (invoice creation, course subscription flags and the notification).
func (s *PaymentStates) WithPaidFlow(store domain.PaymentPaidFlowStore, notifier *SubscriptionNotifier) *PaymentStates {
	s.paid, s.notifier = store, notifier
	return s
}

// paymentStateReceiptArg is send_receipt_email as a task argument: the raw
// request value (None, bool, str or int); other values are not ported.
func paymentStateReceiptArg(value any) (any, error) {
	switch v := value.(type) {
	case nil, bool, string:
		return v, nil
	case json.Number:
		if number, err := strconv.ParseInt(string(v), 10, 64); err == nil {
			return number, nil
		}
	}
	return nil, domain.ErrPaymentStateNotPorted
}

// PaymentStateRequest carries the request values the views read besides the body.
type PaymentStateRequest struct {
	ID          string
	GroupHeader string
	Audit       domain.LoginAuditContext
	// Authorization is request.headers.get('authorization') (approve's invoice task).
	Authorization *string
}

// PaymentStateUUID is is_valid_uuid: a 400 list for a malformed id.
func PaymentStateUUID(raw string) (string, error) { return messageReadsParseUUID(raw) }

func paymentStateScope(identity domain.ResolvedIdentity) *string {
	if identity.ImpersonationAssociationID == "" {
		return nil
	}
	scope := identity.ImpersonationAssociationID
	return &scope
}

// association is scoped_queryset(request, SportAssociation.objects).get(user=request.user):
// athletes and owners without a live association are a 500.
func (s *PaymentStates) association(ctx context.Context, tx domain.PaymentStateTransaction, identity domain.ResolvedIdentity) (string, error) {
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentStateScope(identity))
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return "", domain.ErrPaymentStateServer
	}
	return association, err
}

// payment mirrors scoped_queryset(request, Payment.objects).filter(payment_id=uid).first()
// and the view's association check (PermissionDenied('User not allowed.')).
func (s *PaymentStates) payment(ctx context.Context, tx domain.PaymentStateTransaction, identity domain.ResolvedIdentity, id, association string,
	group *string) (*domain.PaymentStateRow, error) {
	row, err := tx.PaymentState(ctx, domain.PaymentStateQuery{ID: id, Scope: paymentStateScope(identity), Group: group})
	if err != nil {
		return nil, err
	}
	if row == nil || row.AssociationID == "" {
		return nil, domain.ErrPaymentStateServer
	}
	if row.AssociationID != association {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
	}
	return row, nil
}

func (s *PaymentStates) render(ctx context.Context, tx domain.PaymentStateTransaction, id string, group *string) (map[string]any, error) {
	render, err := tx.RenderPayment(ctx, id, group)
	if err != nil {
		return nil, err
	}
	return dto.PaymentStateItem(render)
}

// paymentStateDate is datetime.strptime(value, '%Y-%m-%d') made aware in UTC
// (TIME_ZONE): a value that is not a matching string is a 500.
var paymentStateDatePattern = regexp.MustCompile(`^(\d{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])$`)

func paymentStateDate(value any) (*time.Time, error) {
	if text, ok := value.(string); ok {
		for _, r := range text {
			if r > unicode.MaxASCII && unicode.IsDigit(r) {
				// strptime's \d also matches non-ASCII decimal digits: paymentStateDateAny serves them.
				return nil, domain.ErrPaymentStateNotPorted
			}
		}
	}
	return paymentStateDateAny(value)
}

// paymentStateDateAnyPattern is the strptime pattern with Python's \d: the
// year and the day's second digit accept decimal digits of any script.
var paymentStateDateAnyPattern = regexp.MustCompile(`^(\p{Nd}{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])$`)

// paymentStateDateAny is paymentStateDate with those digits read by int().
func paymentStateDateAny(value any) (*time.Time, error) {
	if value == nil {
		return nil, nil
	}
	text, ok := value.(string)
	if !ok {
		return nil, domain.ErrPaymentStateServer
	}
	if !paymentStateDateAnyPattern.MatchString(text) {
		return nil, domain.ErrPaymentStateServer
	}
	parts := paymentStateDatePattern.FindStringSubmatch(dto.PythonASCIIDigits(text))
	if parts == nil {
		return nil, domain.ErrPaymentStateServer
	}
	year, _ := strconv.Atoi(parts[1])
	month, _ := strconv.Atoi(parts[2])
	day, _ := strconv.Atoi(strings.TrimPrefix(parts[3], " "))
	moment := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if year < 1 || moment.Day() != day || int(moment.Month()) != month {
		return nil, domain.ErrPaymentStateServer
	}
	return &moment, nil
}

func (s *PaymentStates) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// saveChanges is Payment.save()'s defaults plus auditlog's diff of the view's
// own changes (paid, payment_date).
func (s *PaymentStates) save(ctx context.Context, tx domain.PaymentStateTransaction, identity domain.ResolvedIdentity, row *domain.PaymentStateRow,
	paid bool, paymentDate *time.Time, audit domain.LoginAuditContext) error {
	changes := map[string][2]string{}
	if row.Paid != paid {
		changes["paid"] = [2]string{pythonBool(row.Paid), pythonBool(paid)}
	}
	if before, after := courseSubscriptionAuditTime(row.PaymentDate), courseSubscriptionAuditTime(paymentDate); before != after {
		changes["payment_date"] = [2]string{before, after}
	}
	if err := paymentSaveDefaults(ctx, tx, &row.PaymentSaveRow, changes); err != nil {
		return err
	}
	if err := tx.SavePaymentState(ctx, domain.PaymentStateSave{ID: row.ID, Paid: paid, PaymentDate: paymentDate, CategoryID: row.CategoryID,
		Description: row.Description}); err != nil {
		return err
	}
	row.Paid, row.PaymentDate = paid, paymentDate
	if len(changes) == 0 {
		return nil
	}
	return tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		Context: paymentCategoryAuditContext(identity, audit), At: s.at()})
}

// Approve follows payment_approve: is_valid_uuid, the body's payment_date,
// the association, then atomically the payment. A paid payment answers with
// the message; an unpaid one is marked paid (an expense also pays its first
// supplier invoice). Unpaid income payments with an associate, supplier or
// instructor run the paid flow (approvePaid) when it is configured.
func (s *PaymentStates) Approve(ctx context.Context, identity domain.ResolvedIdentity, request PaymentStateRequest, body any) (map[string]any, error) {
	id, err := messageReadsParseUUID(request.ID)
	if err != nil {
		return nil, err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrPaymentStateServer
	}
	value, _ := object.Lookup("payment_date")
	sent, err := paymentStateDate(value)
	if err != nil {
		return nil, err
	}
	var tx domain.PaymentStateTransaction
	if s.paid != nil && s.notifier != nil {
		tx, err = s.paid.BeginPaymentPaidFlow(ctx)
	} else {
		tx, err = s.store.BeginPaymentState(ctx)
	}
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	group := supplierGroup(request.GroupHeader)
	row, err := s.payment(ctx, tx, identity, id, association, group)
	if err != nil {
		return nil, err
	}
	// generate_invoice (default True) only matters for a paid invoiced payment:
	// unpaid expenses and payments without a party return before the task.
	generate := true
	if flag, present := object.Lookup("generate_invoice"); present {
		generate = dto.PythonTruthy(flag)
	}
	if row.Paid {
		dispatch := row.InvoiceID != nil && generate
		var receipt any = false
		if dispatch {
			if s.tasks == nil {
				return nil, domain.ErrPaymentStateNotPorted
			}
			if value, present := object.Lookup("send_receipt_email"); present {
				if receipt, err = paymentStateReceiptArg(value); err != nil {
					return nil, err
				}
			}
		}
		item, err := s.render(ctx, tx, row.ID, group)
		if err != nil {
			return nil, err
		}
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		if dispatch {
			// transaction.on_commit(..., robust=True): a broker failure is logged, the answer stays 200.
			if err := printInvoice(ctx, s.tasks, *row.InvoiceID, authorizationArg(request.Authorization), receipt); err != nil {
				s.log.Error("payment approve: invoice task not queued", "payment_id", row.ID, "error", err)
			}
			item["invoice_generating"] = true
		}
		return map[string]any{"data": map[string]any{"msg": "payment mark as payed.", "payment": item}}, nil
	}
	if !row.Expense && (row.AssociateID != nil || row.SupplierID != nil || row.InstructorID != nil) {
		flow, ok := tx.(domain.PaymentPaidFlowTransaction)
		if !ok {
			return nil, domain.ErrPaymentStateNotPorted
		}
		return s.approvePaid(ctx, flow, identity, request, object, row, association, group, sent, generate)
	}
	paymentDate := row.PaymentDate
	if sent != nil {
		paymentDate = sent
	} else if paymentDate == nil {
		now := s.at()
		paymentDate = &now
	}
	if err = s.save(ctx, tx, identity, row, true, paymentDate, request.Audit); err != nil {
		return nil, err
	}
	if row.Expense {
		invoice, err := tx.SupplierInvoice(ctx, row.ID)
		if err != nil {
			return nil, err
		}
		if invoice != nil {
			changes := map[string][2]string{}
			if !invoice.Paid {
				changes["paid"] = [2]string{"False", "True"}
			}
			if before, after := courseSubscriptionAuditTime(invoice.PaymentDate), courseSubscriptionAuditTime(paymentDate); before != after {
				changes["payment_date"] = [2]string{before, after}
			}
			if err = tx.SaveSupplierInvoice(ctx, invoice.ID, true, paymentDate); err != nil {
				return nil, err
			}
			if err = tx.AppendSupplierInvoiceAudit(ctx, *invoice, changes, paymentCategoryAuditContext(identity, request.Audit), s.at()); err != nil {
				return nil, err
			}
		}
	}
	item, err := s.render(ctx, tx, row.ID, group)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"payment": item}}, tx.Commit()
}

// Cancel follows payment_cancel (no UUID check: a value the column rejects is
// a 500): the payment is serialized before an unpaid save. A paid invoiced
// payment is serialized after the save (its in-memory invoice cancelled, the
// stored meta unchanged), then its invoice is saved cancelled with the
// payment_obj meta and print_document_invoice is published (apply_async).
func (s *PaymentStates) Cancel(ctx context.Context, identity domain.ResolvedIdentity, request PaymentStateRequest) (map[string]any, error) {
	tx, err := s.store.BeginPaymentState(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	id, err := paymentInfoParseUUID(request.ID)
	if err != nil {
		return nil, domain.ErrPaymentStateServer
	}
	group := supplierGroup(request.GroupHeader)
	row, err := s.payment(ctx, tx, identity, id.String(), association, group)
	if err != nil {
		return nil, err
	}
	if row.InvoiceID != nil {
		if !row.Paid {
			return nil, domain.ErrPaymentStateNotPaid
		}
		return s.cancelInvoiced(ctx, tx, identity, request, row, group)
	}
	item, err := s.render(ctx, tx, row.ID, group)
	if err != nil {
		return nil, err
	}
	if !row.Paid {
		return nil, domain.ErrPaymentStateNotPaid
	}
	if err = s.save(ctx, tx, identity, row, false, row.PaymentDate, request.Audit); err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"msg": "payment cancelled.", "payment": item}}, tx.Commit()
}

// Delete follows payment_delete: is_valid_uuid, the association, the payment
// of request.user.sport_association (none: 404), then payment.delete(). An
// invoiced payment first gets its invoice saved cancelled with the
// payment_obj meta (committed, as the request is not atomic), then
// print_document_invoice runs eagerly, then the payment is deleted.
func (s *PaymentStates) Delete(ctx context.Context, identity domain.ResolvedIdentity, request PaymentStateRequest) (map[string]any, error) {
	id, err := messageReadsParseUUID(request.ID)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginPaymentState(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	group := supplierGroup(request.GroupHeader)
	row, err := tx.PaymentState(ctx, domain.PaymentStateQuery{ID: id, Association: &association, Scope: paymentStateScope(identity), Group: group})
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrPaymentStateNotFound
	}
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return nil, err
	}
	if row.InvoiceID != nil {
		if s.printer == nil {
			return nil, domain.ErrPaymentStateNotPorted
		}
		if err = s.cancelInvoice(ctx, tx, identity, request, row.ID, *row.InvoiceID, group); err != nil {
			return nil, err
		}
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		if err = s.printer.Invoice(ctx, *row.InvoiceID, request.Authorization); err != nil {
			return nil, err
		}
		if tx, err = s.store.BeginPaymentState(ctx); err != nil {
			return nil, err
		}
		defer tx.Rollback()
	}
	audit := domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, request.Audit), ActorAssociation: actor, At: s.at(),
		Group: group}
	if err = tx.DeletePayment(ctx, row.ID, audit); err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"msg": "payment deleted."}}, tx.Commit()
}

// cancelInvoiced is payment_cancel's invoiced branch: payment.save() unpaid,
// the response serialization, the invoice save, then the task dispatch.
func (s *PaymentStates) cancelInvoiced(ctx context.Context, tx domain.PaymentStateTransaction, identity domain.ResolvedIdentity,
	request PaymentStateRequest, row *domain.PaymentStateRow, group *string) (map[string]any, error) {
	if s.tasks == nil {
		return nil, domain.ErrPaymentStateNotPorted
	}
	if err := s.save(ctx, tx, identity, row, false, row.PaymentDate, request.Audit); err != nil {
		return nil, err
	}
	item, err := s.render(ctx, tx, row.ID, group)
	if err != nil {
		return nil, err
	}
	invoice, ok := item["invoice"].(map[string]any)
	if !ok {
		return nil, domain.ErrPaymentStateServer
	}
	invoice["cancelled"] = true
	if err = s.cancelInvoice(ctx, tx, identity, request, row.ID, *row.InvoiceID, group); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	if err = printInvoice(ctx, s.tasks, *row.InvoiceID, authorizationArg(request.Authorization)); err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"msg": "payment cancelled.", "payment": item}}, nil
}

// cancelInvoice serializes the payment as stored (PaymentSerializer) into the
// view's payment_obj and saves the invoice cancelled with it as meta.
func (s *PaymentStates) cancelInvoice(ctx context.Context, tx domain.PaymentStateTransaction, identity domain.ResolvedIdentity,
	request PaymentStateRequest, payment, id string, group *string) error {
	render, err := tx.RenderPayment(ctx, payment, group)
	if err != nil {
		return err
	}
	invoice, err := tx.PaymentStateInvoice(ctx, id)
	if err != nil {
		return err
	}
	if invoice == nil {
		return domain.ErrPaymentStateServer
	}
	meta := dto.PaymentStateInvoiceMeta(render.Payment)
	return tx.CancelPaymentStateInvoice(ctx, *invoice, string(dto.PythonJSONDumps(meta)), string(dto.PythonJSONDumpsSorted(meta)),
		paymentCategoryAuditContext(identity, request.Audit), s.at())
}

// Mark follows payment_bulk_archive / payment_bulk_delete: 'payment_ids' must
// be a key of the body, then bulk_update of column on the association's
// matching live rows (no signals, no audit).
func (s *PaymentStates) Mark(ctx context.Context, identity domain.ResolvedIdentity, request PaymentStateRequest, body any, column string) error {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return domain.ErrPaymentStateServer
	}
	value, present := object.Lookup("payment_ids")
	if !present {
		return domain.ErrPaymentStateServer
	}
	tx, err := s.store.BeginPaymentState(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return err
	}
	ids, err := bulkIDs(value)
	if err != nil {
		return domain.ErrPaymentStateServer
	}
	if err = tx.MarkPayments(ctx, column, ids, association, paymentStateScope(identity), supplierGroup(request.GroupHeader)); err != nil {
		return err
	}
	return tx.Commit()
}
