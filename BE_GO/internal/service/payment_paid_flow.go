package service

import (
	"context"
	"fmt"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// paymentPaidFlowNotification is send_notification's call, delivered after
// the commit once validate_broadcast's rows were written in the transaction,
// and, for mark_payment_as_paid, the print_document_invoice.delay that
// transaction.on_commit runs once the block committed (after the
// notification, which Django sends inside the block).
type paymentPaidFlowNotification struct {
	user, message, association string
	subscribed                 bool
	print                      *paymentPaidFlowPrint
}

// paymentPaidFlowPrint is the on-commit print_document_invoice.delay(invoice, token).
type paymentPaidFlowPrint struct {
	publisher celery.Publisher
	invoice   string
	token     any
}

func (n *paymentPaidFlowNotification) deliver(ctx context.Context, notifier *SubscriptionNotifier) error {
	if n == nil {
		return nil
	}
	if err := notifier.DeliverValidated(ctx, n.user, "payment", n.message, n.association, n.subscribed); err != nil {
		return err
	}
	if n.print == nil {
		return nil
	}
	return paymentPaidFlowPublish(ctx, n.print.publisher, n.print.invoice, n.print.token)
}

// stripeMarkPaid is mark_payment_as_paid's first save of an unpaid payment:
// payment.paid = True, payment.payment_date = now, payment.type = 'stripe' and
// payment.save(update_fields=['paid', 'payment_date', 'type']). Payment.save()
// computes its defaults (a failure raises inside the block) but only the
// update fields are written; auditlog records the changed update fields.
func stripeMarkPaid(ctx context.Context, tx domain.PaymentPaidFlowTransaction, row domain.PaymentSaveRow, before *time.Time,
	audit domain.LoginAuditContext, now time.Time) error {
	if err := paymentSaveDefaults(ctx, tx, &row, map[string][2]string{}); err != nil {
		return err
	}
	previous, err := tx.MarkStripePaymentPaid(ctx, row.ID, now)
	if err != nil {
		return err
	}
	changes := map[string][2]string{"paid": {"False", "True"}}
	if old, current := courseSubscriptionAuditTime(before), courseSubscriptionAuditTime(&now); old != current {
		changes["payment_date"] = [2]string{old, current}
	}
	if previous != "stripe" {
		changes["type"] = [2]string{previous, "stripe"}
	}
	return tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		Context: audit, At: now})
}

// paymentPaidFlowInvoiceGroup is the group Invoice.objects.create stores: the
// raw X-Group-Id (a non-canonical spelling is not ported).
func paymentPaidFlowInvoiceGroup(header string) (*string, error) {
	if header == "" {
		return nil, nil
	}
	parsed, err := uuid.Parse(header)
	if err != nil {
		return nil, domain.ErrPaymentStateServer
	}
	if parsed.String() != header {
		return nil, domain.ErrPaymentStateNotPorted
	}
	return &header, nil
}

// paymentPaidFlowMessage is the notification text both views build:
// 'Pagamento di € <amount with a comma>', 'per <description>' (no space) when
// there is one, '.', then the associate's or the user's full name.
func paymentPaidFlowMessage(amount, description string, people domain.PaymentPaidFlowPeople) string {
	message := "Pagamento di € " + strings.ReplaceAll(amount, ".", ",")
	if description != "" {
		message += "per " + description
	}
	message += "."
	if people.AssociateFound {
		message += " Pagamento dell'associato " + fullName(people.AssociateFirst, people.AssociateLast) + "."
	} else if people.UserFound {
		message += " Pagamento dell'utente " + fullName(people.UserFirst, people.UserLast) + "."
	}
	return message
}

// paymentPaidFlowFloat is float(value) of a stored JSON amount: booleans,
// strings and numbers; other types are a TypeError (500).
func paymentPaidFlowFloat(value any) (float64, error) {
	if flag, ok := value.(bool); ok {
		if flag {
			return 1, nil
		}
		return 0, nil
	}
	return invoiceFloatOfStr(value)
}

// paymentPaidFlowStripeFees is mark_payment_as_paid's fee arithmetic: like
// the approve view's, except that float() reads the amounts directly and the
// membership fee is only recomputed when there are meta payment categories.
func paymentPaidFlowStripeFees(subject int, payment domain.InvoiceGeneratePayment) (invoiceFeePair, error) {
	if subject != 1 {
		return invoiceFees(subject, payment)
	}
	zero, _ := floatFee(0)
	pair := invoiceFeePair{membership: decimalFee(payment.Amount), activity: zero}
	if payment.MetaPaymentCategories == nil {
		return pair, nil
	}
	parsed, err := dto.ParseReferenceJSON([]byte(*payment.MetaPaymentCategories))
	if err != nil {
		return pair, err
	}
	if !dto.PythonTruthy(parsed) {
		return pair, nil
	}
	items, ok := parsed.([]any)
	if !ok {
		return pair, domain.ErrPaymentStateServer
	}
	activity := 0.0
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return pair, domain.ErrPaymentStateServer
		}
		value, present := object.Lookup("amount")
		if !present {
			return pair, domain.ErrPaymentStateServer
		}
		if !dto.PythonTruthy(value) {
			continue
		}
		number, err := paymentPaidFlowFloat(value)
		if err != nil {
			return pair, err
		}
		activity += number
	}
	if pair.activity, err = floatFee(activity); err != nil {
		return pair, err
	}
	amount, ok := new(big.Rat).SetString(payment.Amount)
	if !ok {
		return pair, domain.ErrPaymentStateServer
	}
	if amount.Sign() <= 0 {
		// membership_fee > 0 is false: it stays the stored Decimal.
		return pair, nil
	}
	base, ok := dto.PythonFloatParse(payment.Amount)
	if !ok {
		return pair, domain.ErrPaymentStateServer
	}
	if pair.membership, err = floatFee(base - activity); err != nil {
		return pair, err
	}
	return pair, nil
}

// paymentPaidFlowCourses is the views' course branch (subject 2): the first
// course subscription, else the first installment's course subscription
// (approve) or the installment itself (Stripe), else the first carnet
// subscription's course subscriptions (approve) or the carnet subscription
// (Stripe, whose save changes nothing). It returns the notification's description.
func paymentPaidFlowCourses(ctx context.Context, tx domain.PaymentPaidFlowTransaction, payment string, scope, group *string, stripe bool,
	audit domain.CourseSubscriptionAudit) (string, error) {
	course, err := tx.PaidFlowCourseSubscription(ctx, payment, scope)
	if err != nil {
		return "", err
	}
	if course != nil {
		if err = tx.MarkCourseSubscriptionPaid(ctx, *course, audit); err != nil {
			return "", err
		}
		return `il corso "` + pythonOptional(course.CourseTitle) + `"`, nil
	}
	installment, err := tx.PaidFlowInstallment(ctx, payment, scope, group)
	if err != nil {
		return "", err
	}
	if installment != nil {
		if stripe {
			if err = tx.MarkInstallmentPaid(ctx, *installment, audit); err != nil {
				return "", err
			}
			return "rata del corso " + pythonOptional(installment.Parent.CourseTitle), nil
		}
		if err = tx.MarkCourseSubscriptionPaid(ctx, installment.Parent, audit); err != nil {
			return "", err
		}
		return `il corso "` + pythonOptional(installment.Parent.CourseTitle) + `"`, nil
	}
	carnet, err := tx.PaidFlowCarnet(ctx, payment, scope)
	if err != nil {
		return "", err
	}
	if stripe {
		// is_carnet is set even without a carnet subscription; its title's
		// AttributeError is caught ("carnet").
		if carnet == nil || !carnet.CarnetFound {
			return "carnet", nil
		}
		return "carnet " + pythonOptional(carnet.Title), nil
	}
	if carnet == nil {
		// course_subscription.course raises: the view's except keeps 'il corso'.
		return "il corso", nil
	}
	rows, err := tx.PaidFlowCarnetCourses(ctx, carnet.ID)
	if err != nil {
		return "", err
	}
	for _, row := range rows {
		if err = tx.MarkCourseSubscriptionPaid(ctx, row, audit); err != nil {
			return "", err
		}
	}
	if !carnet.CarnetFound {
		// carnet_sub.carnet_id.title outside a try: the request fails and rolls back.
		return "", domain.ErrPaymentStateServer
	}
	return "carnet " + pythonOptional(carnet.Title), nil
}

// paymentPaidFlowPublish is print_document_invoice.delay(str(invoice_id), token) (a tuple of args).
func paymentPaidFlowPublish(ctx context.Context, publisher celery.Publisher, invoice string, token any) error {
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), invoicePublishTimeout)
	defer cancel()
	if _, err := publisher.Publish(publish, celery.Task{Name: "print_document_invoice", Args: []any{invoice, token}}); err != nil {
		return fmt.Errorf("print_document_invoice: %w", err)
	}
	return nil
}

// paymentPaidFlowCourseAudit is the course subscription entries' context.
func paymentPaidFlowCourseAudit(ctx context.Context, tx domain.PaymentPaidFlowTransaction, identity domain.ResolvedIdentity,
	audit domain.LoginAuditContext, at time.Time, group *string) (domain.CourseSubscriptionAudit, error) {
	result := domain.CourseSubscriptionAudit{Context: audit, At: at, Group: group}
	if identity.Actor != nil && audit.ActorID != nil {
		actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
		if err != nil {
			return result, err
		}
		result.ActorAssociation = actor
	}
	return result, nil
}

// approvePaid is payment_approve for an unpaid income payment with an
// associate, supplier or instructor: the fees, the next invoice number by
// payment date (advisory lock), the description, the invoice (associate or
// supplier only) created and saved again, the payment save, its first
// supplier invoice, the course subscription paid flags and the notification
// to request.user (validate_broadcast in the transaction, the Redis list and
// the Channels push after the commit), then the invoice task on commit.
func (s *PaymentStates) approvePaid(ctx context.Context, tx domain.PaymentPaidFlowTransaction, identity domain.ResolvedIdentity,
	request PaymentStateRequest, object domain.RequestObject, row *domain.PaymentStateRow, association string, group *string, sent *time.Time,
	generate bool) (map[string]any, error) {
	paymentDate := row.PaymentDate
	if sent != nil {
		paymentDate = sent
	} else if paymentDate == nil {
		now := s.at()
		paymentDate = &now
	}
	var receipt any = false
	if value, present := object.Lookup("send_receipt_email"); present {
		var err error
		if receipt, err = paymentStateReceiptArg(value); err != nil {
			return nil, err
		}
	}
	user := identity.ScopeUser
	if user == nil || (user.Role != domain.RoleAssociation && user.Role != domain.RoleCollaborator) {
		// send_notification without an association id: not ported.
		return nil, domain.ErrPaymentStateNotPorted
	}
	payment, err := tx.InvoiceGeneratePayment(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	fees, err := invoiceFees(row.Subject, payment)
	if err != nil {
		return nil, err
	}
	owner, err := tx.InvoiceGenerateOwner(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if err = tx.LockInvoiceNumbering(ctx, association); err != nil {
		return nil, err
	}
	from, _, err := paymentMiscBalanceYear(s.now().UTC(), owner.StartDay, owner.StartMonth)
	if err != nil {
		return nil, domain.ErrPaymentStateServer
	}
	last, err := tx.LastInvoiceNumberByPaymentDate(ctx, association, group, paymentDate.Before(from), from)
	if err != nil {
		return nil, err
	}
	number := int64(0)
	if last != nil {
		number = *last
	}
	number = max(number, int64(owner.StartingNumber)) + 1
	description, err := (&InvoiceGenerations{}).description(ctx, tx, row, payment, row.AssociateID, association, group)
	if err != nil {
		return nil, err
	}
	at := s.at()
	audit := paymentCategoryAuditContext(identity, request.Audit)
	invoice := row.InvoiceID
	var created *invoiceFeePair
	if row.SupplierID != nil || row.AssociateID != nil {
		invoiceGroup, err := paymentPaidFlowInvoiceGroup(request.GroupHeader)
		if err != nil {
			return nil, err
		}
		create := domain.InvoiceGenerateCreate{ID: uuid.NewString(), Association: association, CreationDate: at,
			MembershipFee: fees.membership.stored, ActivityFee: fees.activity.stored, MetaPaymentCategories: payment.MetaPaymentCategories,
			Description: &description, Number: number, Group: invoiceGroup}
		if err = tx.CreateInvoice(ctx, create, map[string]string{"membership_fee": fees.membership.memory,
			"activity_fee": fees.activity.memory}, audit, at); err != nil {
			return nil, err
		}
		// invoice.save(): creation_date (payment_date_equal_invoice_date) and the float fees.
		changes := map[string][2]string{}
		date := at
		if owner.PaymentDateEqual {
			date = *paymentDate
			if before, after := courseSubscriptionAuditTime(&at), courseSubscriptionAuditTime(paymentDate); before != after {
				changes["creation_date"] = [2]string{before, after}
			}
		}
		for field, fee := range map[string]invoiceFee{"membership_fee": fees.membership, "activity_fee": fees.activity} {
			if fee.rendered != fee.memory {
				changes[field] = [2]string{fee.rendered, fee.memory}
			}
		}
		if err = tx.SetInvoiceCreationDate(ctx, create.ID, association, date, changes, audit, at); err != nil {
			return nil, err
		}
		invoice, created = &create.ID, &fees
	}
	changes := map[string][2]string{"paid": {"False", "True"}}
	if before, after := courseSubscriptionAuditTime(row.PaymentDate), courseSubscriptionAuditTime(paymentDate); before != after {
		changes["payment_date"] = [2]string{before, after}
	}
	if !samePointer(row.InvoiceID, invoice) {
		changes["invoice"] = [2]string{pythonOptional(row.InvoiceID), pythonOptional(invoice)}
	}
	if err = paymentSaveDefaults(ctx, tx, &row.PaymentSaveRow, changes); err != nil {
		return nil, err
	}
	if err = tx.SavePaidFlowPayment(ctx, domain.PaymentPaidFlowSave{ID: row.ID, PaymentDate: *paymentDate, Invoice: invoice,
		CategoryID: row.CategoryID, Description: row.Description}); err != nil {
		return nil, err
	}
	if err = tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		Context: audit, At: at}); err != nil {
		return nil, err
	}
	row.Paid, row.PaymentDate, row.InvoiceID = true, paymentDate, invoice
	if err = s.paySupplierInvoice(ctx, tx, row.ID, audit); err != nil {
		return nil, err
	}
	var notified string
	switch row.Subject {
	case 2:
		courseAudit, err := paymentPaidFlowCourseAudit(ctx, tx, identity, audit, at, group)
		if err != nil {
			return nil, err
		}
		if notified, err = paymentPaidFlowCourses(ctx, tx, row.ID, paymentStateScope(identity), group, false, courseAudit); err != nil {
			return nil, err
		}
	case 1:
		notified = "l'iscrizione all'associazione sportiva"
	default:
		notified = pythonOptionalEmpty(row.Description)
	}
	people, err := tx.PaidFlowPeople(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	message := paymentPaidFlowMessage(payment.Amount, notified, people)
	subscribed, err := tx.ValidateBroadcast(ctx, user.ID, "broadcast_"+user.ID, audit, at)
	if err != nil {
		return nil, err
	}
	item, err := s.render(ctx, tx, row.ID, group)
	if err != nil {
		return nil, err
	}
	if rendered, ok := item["invoice"].(map[string]any); ok && created != nil {
		// The in-memory invoice keeps the float fees.
		rendered["membership_fee"], rendered["activity_fee"] = created.membership.rendered, created.activity.rendered
	}
	dispatch := invoice != nil && generate
	if dispatch && s.tasks == nil {
		return nil, domain.ErrPaymentStateNotPorted
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	notification := &paymentPaidFlowNotification{user: user.ID, message: message, association: association, subscribed: subscribed}
	if err = notification.deliver(ctx, s.notifier); err != nil {
		return nil, err
	}
	if dispatch {
		// transaction.on_commit(..., robust=True): a broker failure is logged, the answer stays 200.
		if err := printInvoice(ctx, s.tasks, *invoice, authorizationArg(request.Authorization), receipt); err != nil {
			s.log.Error("payment approve: invoice task not queued", "payment_id", row.ID, "error", err)
		}
		item["invoice_generating"] = true
	}
	return map[string]any{"data": map[string]any{"msg": "payment mark as payed.", "payment": item}}, nil
}

// paySupplierInvoice marks the payment's first supplier invoice paid now.
func (s *PaymentStates) paySupplierInvoice(ctx context.Context, tx domain.PaymentStateTransaction, payment string, audit domain.LoginAuditContext) error {
	invoice, err := tx.SupplierInvoice(ctx, payment)
	if err != nil || invoice == nil {
		return err
	}
	now := s.at()
	changes := map[string][2]string{}
	if !invoice.Paid {
		changes["paid"] = [2]string{"False", "True"}
	}
	if before, after := courseSubscriptionAuditTime(invoice.PaymentDate), courseSubscriptionAuditTime(&now); before != after {
		changes["payment_date"] = [2]string{before, after}
	}
	if err = tx.SaveSupplierInvoice(ctx, invoice.ID, true, &now); err != nil {
		return err
	}
	return tx.AppendSupplierInvoiceAudit(ctx, *invoice, changes, audit, now)
}

// stripePaidFlow is mark_payment_as_paid for an unpaid payment without an
// invoice, inside the mark's transaction after stripeMarkPaid: the fees, the
// next number (the association's invoices created in the current balance
// year, no lock), the description, the invoice, the payment save recording it,
// the course subscription or installment paid flag, validate_broadcast for
// payment.user and the on-commit print_document_invoice.delay (returned with
// the notification, published after the commit).
func (s *StripeRoutes) stripePaidFlow(ctx context.Context, tx domain.PaymentPaidFlowTransaction, identity domain.ResolvedIdentity,
	request StripeRequest, id string, now time.Time, withRequest bool) (*paymentPaidFlowNotification, error) {
	group := supplierGroup(request.GroupHeader)
	row, err := tx.PaymentState(ctx, domain.PaymentStateQuery{ID: id, Group: group})
	if err != nil {
		return nil, err
	}
	if row == nil || row.AssociationID == "" {
		return nil, domain.ErrStripeServer
	}
	// The update_fields save already ran Payment.save()'s defaults on the
	// in-memory payment: the invoice description reads them, and the later
	// full save records them with the invoice.
	changes := map[string][2]string{}
	if err = paymentSaveDefaults(ctx, tx, &row.PaymentSaveRow, changes); err != nil {
		return nil, err
	}
	payment, err := tx.InvoiceGeneratePayment(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	fees, err := paymentPaidFlowStripeFees(row.Subject, payment)
	if err != nil {
		return nil, err
	}
	_, owner, err := tx.PaidFlowOwner(ctx, row.AssociationID)
	if err != nil {
		return nil, err
	}
	from, to, err := paymentMiscBalanceYear(s.now().UTC(), owner.StartDay, owner.StartMonth)
	if err != nil {
		return nil, domain.ErrStripeServer
	}
	last, err := tx.MaxInvoiceNumberCreated(ctx, row.AssociationID, group, from, to)
	if err != nil {
		return nil, err
	}
	number := int64(0)
	if last != nil {
		number = *last
	}
	number = max(number, int64(owner.StartingNumber)) + 1
	description, err := (&InvoiceGenerations{}).description(ctx, tx, row, payment, row.AssociateID, row.AssociationID, group)
	if err != nil {
		return nil, err
	}
	invoiceGroup, err := paymentPaidFlowInvoiceGroup(request.GroupHeader)
	if err != nil {
		return nil, err
	}
	audit := stripeAuditContext(identity, request.Audit)
	create := domain.InvoiceGenerateCreate{ID: uuid.NewString(), Association: row.AssociationID, CreationDate: now,
		MembershipFee: fees.membership.stored, ActivityFee: fees.activity.stored, MetaPaymentCategories: payment.MetaPaymentCategories,
		Description: &description, Number: number, Group: invoiceGroup}
	if err = tx.CreateInvoice(ctx, create, map[string]string{"membership_fee": fees.membership.memory,
		"activity_fee": fees.activity.memory}, audit, now); err != nil {
		return nil, err
	}
	// paid, payment_date and type were saved by stripeMarkPaid: only the invoice and the defaults change.
	changes["invoice"] = [2]string{"None", create.ID}
	if err = tx.SavePaidFlowPayment(ctx, domain.PaymentPaidFlowSave{ID: row.ID, PaymentDate: now, Invoice: &create.ID,
		CategoryID: row.CategoryID, Description: row.Description}); err != nil {
		return nil, err
	}
	if err = tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		Context: audit, At: now}); err != nil {
		return nil, err
	}
	notified := ""
	if row.Subject == 2 {
		courseAudit, err := paymentPaidFlowCourseAudit(ctx, tx, identity, audit, now, group)
		if err != nil {
			return nil, err
		}
		if notified, err = paymentPaidFlowCourses(ctx, tx, row.ID, nil, group, true, courseAudit); err != nil {
			return nil, err
		}
	}
	var token any = s.bypassToken
	if withRequest {
		token = authorizationArg(request.Authorization)
	}
	switch row.Subject {
	case 2:
	case 1:
		notified = "l'iscrizione all'associazione sportiva"
	default:
		notified = pythonOptionalEmpty(row.Description)
	}
	people, err := tx.PaidFlowPeople(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	if !people.UserFound {
		// send_notification(None, ...): AttributeError after the task was published; the block rolls back.
		return nil, domain.ErrStripeServer
	}
	message := paymentPaidFlowMessage(payment.Amount, notified, people)
	subscribed, err := tx.ValidateBroadcast(ctx, *people.UserID, "broadcast_"+*people.UserID, audit, now)
	if err != nil {
		return nil, err
	}
	return &paymentPaidFlowNotification{user: *people.UserID, message: message, association: row.AssociationID, subscribed: subscribed,
		print: &paymentPaidFlowPrint{publisher: s.publisher, invoice: create.ID, token: token}}, nil
}
