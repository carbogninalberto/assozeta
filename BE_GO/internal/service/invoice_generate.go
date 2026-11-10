package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// invoicePublishTimeout bounds the print_document_invoice publish after the commit.
const invoicePublishTimeout = 10 * time.Second

// InvoiceGenerations ports payment_generate_invoice.
type InvoiceGenerations struct {
	// anyMetaName formats every meta.subscription_data.name with str().
	anyMetaName bool
	store       domain.InvoiceGenerateStore
	publisher   celery.Publisher
	now         func() time.Time
	newID       func() string
}

func NewInvoiceGenerations(store domain.InvoiceGenerateStore, publisher celery.Publisher, now func() time.Time) *InvoiceGenerations {
	return &InvoiceGenerations{anyMetaName: true, store: store, publisher: publisher, now: now, newID: uuid.NewString}
}

// InvoiceGenerateRequest carries the request values besides the body.
type InvoiceGenerateRequest struct {
	ID            string
	GroupHeader   string
	Authorization *string
	Audit         domain.LoginAuditContext
}

// printInvoice is print_document_invoice.apply_async(args=[str(invoice_id), authorization, ...]).
func printInvoice(ctx context.Context, publisher celery.Publisher, args ...any) error {
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), invoicePublishTimeout)
	defer cancel()
	_, err := publisher.Publish(publish, celery.Task{Name: "print_document_invoice", Args: args, ArgsList: true})
	if err != nil {
		return fmt.Errorf("print_document_invoice: %w", err)
	}
	return nil
}

func authorizationArg(authorization *string) any {
	if authorization == nil {
		return nil
	}
	return *authorization
}

func generatingResponse(item map[string]any, msg string) map[string]any {
	item["invoice_generating"] = true
	return map[string]any{"data": map[string]any{"msg": msg, "payment": item}}
}

// Generate follows payment_generate_invoice: is_valid_uuid, the association,
// request.data.get('only_print'); then either the print dispatch of the
// payment's invoice, or atomically the payment, the subscription, the fees,
// the invoice number (advisory lock), the description, the invoice and the
// payment save, and after the commit the print dispatch.
func (s *InvoiceGenerations) Generate(ctx context.Context, identity domain.ResolvedIdentity, request InvoiceGenerateRequest,
	body any) (map[string]any, error) {
	id, err := messageReadsParseUUID(request.ID)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginInvoiceGenerate(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentStateScope(identity))
	if err != nil {
		if errors.Is(err, domain.ErrAssociationNotFound) {
			return nil, domain.ErrPaymentStateServer
		}
		return nil, err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrPaymentStateServer
	}
	group := supplierGroup(request.GroupHeader)
	if flag, _ := object.Lookup("only_print"); dto.PythonTruthy(flag) {
		row, err := tx.PaymentState(ctx, domain.PaymentStateQuery{ID: id, Scope: paymentStateScope(identity), Group: group})
		if err != nil {
			return nil, err
		}
		if row == nil || row.InvoiceID == nil {
			return nil, domain.ErrPaymentStateServer
		}
		item, err := (&PaymentStates{}).render(ctx, tx, row.ID, group)
		if err != nil {
			return nil, err
		}
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		if err = printInvoice(ctx, s.publisher, *row.InvoiceID, authorizationArg(request.Authorization)); err != nil {
			return nil, err
		}
		return generatingResponse(item, "generated invoice."), nil
	}
	row, err := (&PaymentStates{}).payment(ctx, tx, identity, id, association, group)
	if err != nil {
		return nil, err
	}
	subscriptionValue, _ := object.Lookup("subscription_id")
	if subscriptionValue == nil {
		return nil, domain.ErrInvoiceGenerateSelect
	}
	subscriptionID, err := paymentSignUUID(subscriptionValue)
	if err != nil {
		return nil, err
	}
	found, associate, err := tx.GenerateSubscription(ctx, subscriptionID, paymentStateScope(identity), group)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, domain.ErrPaymentStateServer
	}
	payment, err := tx.InvoiceGeneratePayment(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	fees, err := invoiceFeesAny(row.Subject, payment)
	if err != nil {
		return nil, err
	}
	// Invoice.objects.create is given the raw X-Group-Id: the column stores its uuid, the instance keeps the text as sent
	// (auditlog and the response write it).
	invoiceGroup, sentGroup, err := paymentPaidFlowSentGroup(request.GroupHeader)
	if err != nil {
		return nil, err
	}
	owner, err := tx.InvoiceGenerateOwner(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	number, err := s.nextNumber(ctx, tx, owner, association, group, payment.CreationDate)
	if err != nil {
		return nil, err
	}
	description, err := s.description(ctx, tx, row, payment, associate, association, group)
	if err != nil {
		return nil, err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	audit := paymentCategoryAuditContext(identity, request.Audit)
	create := domain.InvoiceGenerateCreate{ID: s.newID(), Association: association, CreationDate: at, MembershipFee: fees.membership.stored,
		ActivityFee: fees.activity.stored, MetaPaymentCategories: payment.MetaPaymentCategories, Description: &description, Number: number,
		Group: invoiceGroup}
	memory := map[string]string{"membership_fee": fees.membership.memory, "activity_fee": fees.activity.memory}
	if sentGroup != nil {
		memory["group"] = *sentGroup
	}
	if err = tx.CreateInvoice(ctx, create, memory, audit, at); err != nil {
		return nil, err
	}
	if owner.PaymentDateEqual {
		if payment.PaymentDate == nil {
			// creation_date = None: the NOT NULL column rejects the save (500, rolled back).
			return nil, domain.ErrPaymentStateServer
		}
		changes := map[string][2]string{}
		if before, after := courseSubscriptionAuditTime(&at), courseSubscriptionAuditTime(payment.PaymentDate); before != after {
			changes["creation_date"] = [2]string{before, after}
		}
		for field, fee := range map[string]invoiceFee{"membership_fee": fees.membership, "activity_fee": fees.activity} {
			if fee.column != fee.memory {
				changes[field] = [2]string{fee.column, fee.memory}
			}
		}
		if sentGroup != nil && *sentGroup != *invoiceGroup {
			// The re-save compares the stored uuid with the text the instance still holds.
			changes["group"] = [2]string{*invoiceGroup, *sentGroup}
		}
		if err = tx.SetInvoiceCreationDate(ctx, create.ID, association, *payment.PaymentDate, changes, audit, at); err != nil {
			return nil, err
		}
	}
	changes := map[string][2]string{}
	if !row.Paid {
		changes["paid"] = [2]string{"False", "True"}
	}
	if pythonOptional(row.AssociateID) != pythonOptional(associate) {
		changes["associate"] = [2]string{pythonOptional(row.AssociateID), pythonOptional(associate)}
	}
	if pythonOptional(row.InvoiceID) != create.ID {
		changes["invoice"] = [2]string{pythonOptional(row.InvoiceID), create.ID}
	}
	if err = paymentSaveDefaults(ctx, tx, &row.PaymentSaveRow, changes); err != nil {
		return nil, err
	}
	if err = tx.SaveGeneratedPayment(ctx, domain.InvoiceGenerateSave{ID: row.ID, Invoice: create.ID, Associate: associate,
		CategoryID: row.CategoryID, Description: row.Description}); err != nil {
		return nil, err
	}
	if len(changes) > 0 {
		if err = tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
			Context: audit, At: at}); err != nil {
			return nil, err
		}
	}
	item, err := (&PaymentStates{}).render(ctx, tx, row.ID, group)
	if err != nil {
		return nil, err
	}
	if invoice, ok := item["invoice"].(map[string]any); ok {
		invoice["membership_fee"], invoice["activity_fee"] = fees.membership.rendered, fees.activity.rendered
		if sentGroup != nil {
			invoice["group"] = *sentGroup
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	if err = printInvoice(ctx, s.publisher, create.ID, authorizationArg(request.Authorization)); err != nil {
		return nil, err
	}
	return generatingResponse(item, "generated invoice."), nil
}

// nextNumber is InvoiceService.get_next_invoice_number(use_payment_date=False):
// the advisory lock first, then the balance year of now from the owner's
// settings, the last number and starting_number_invoices.
func (s *InvoiceGenerations) nextNumber(ctx context.Context, tx domain.InvoiceGenerateTransaction, settings domain.InvoiceGenerateOwner,
	association string, group *string, reference time.Time) (int64, error) {
	if err := tx.LockInvoiceNumbering(ctx, association); err != nil {
		return 0, err
	}
	from, _, err := paymentMiscBalanceYear(s.now().UTC(), settings.StartDay, settings.StartMonth)
	if err != nil {
		// get_range_from_year_and_starting_date raises ValueError.
		return 0, domain.ErrPaymentStateServer
	}
	last, err := tx.LastInvoiceNumber(ctx, domain.InvoiceGenerateNumberQuery{Association: association, Group: group,
		Before: reference.Before(from), From: from})
	if err != nil {
		return 0, err
	}
	latest := int64(0)
	if last != nil {
		latest = *last
	}
	return max(latest, int64(settings.StartingNumber)) + 1, nil
}

// invoiceFee is one in-memory fee: memory is its str() (auditlog), stored the
// numeric text the column rounds, rendered DRF's two-place string, column the
// value the numeric(9, 2) column keeps (it differs from rendered on a tie).
type invoiceFee struct {
	memory, stored, rendered, column string
	tie                              bool
}

type invoiceFeePair struct{ membership, activity invoiceFee }

func decimalFee(text string) invoiceFee {
	return invoiceFee{memory: text, stored: text, rendered: text, column: text}
}

// floatFee is a Python float fee: Decimal from float at max_digits 9 in the
// column (refused when its two-place rounding is an exact tie, where
// PostgreSQL and DRF round differently), Decimal(str(value)) quantized half
// even in the response.
func floatFee(value float64) (invoiceFee, error) {
	fee, err := floatFeeAny(value)
	if err == nil && fee.tie {
		return invoiceFee{}, domain.ErrPaymentStateNotPorted
	}
	return fee, err
}

// floatFeeAny is floatFee with the ties: the column rounds half away from
// zero, the response half to even.
func floatFeeAny(value float64) (invoiceFee, error) {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return invoiceFee{}, domain.ErrPaymentStateNotPorted
	}
	stored := dto.DecimalFromFloat9(value)
	rat, ok := new(big.Rat).SetString(stored)
	if !ok {
		return invoiceFee{}, domain.ErrPaymentStateNotPorted
	}
	scaled := new(big.Rat).Mul(rat, big.NewRat(1000, 1))
	tie := scaled.IsInt() && new(big.Int).Rem(new(big.Int).Abs(scaled.Num()), big.NewInt(10)).Int64() == 5
	// numeric(9, 2): half away from zero.
	cents := new(big.Rat).Mul(new(big.Rat).Abs(rat), big.NewRat(100, 1))
	cents.Add(cents, big.NewRat(1, 2))
	rounded := new(big.Int).Quo(cents.Num(), cents.Denom())
	column := new(big.Rat).SetFrac(rounded, big.NewInt(100)).FloatString(2)
	if rat.Sign() < 0 && rounded.Sign() != 0 {
		column = "-" + column
	}
	return invoiceFee{memory: dto.PythonFloatStr(value), stored: stored, rendered: dto.PaymentFloatHalfEven(value), column: column,
		tie: tie}, nil
}

// invoiceFees is the view's fee arithmetic: a subscription payment's
// activity fee is the float sum of the truthy meta_payment_categories amounts
// (float(str(amount))) and its positive amount minus that sum the membership
// fee; other payments have membership 0.0 and the amount as activity fee.
func invoiceFees(subject int, payment domain.InvoiceGeneratePayment) (invoiceFeePair, error) {
	pair, err := invoiceFeesAny(subject, payment)
	if err == nil && (pair.membership.tie || pair.activity.tie) {
		return invoiceFeePair{}, domain.ErrPaymentStateNotPorted
	}
	return pair, err
}

// invoiceFeesAny is invoiceFees with the fees that round on a tie.
func invoiceFeesAny(subject int, payment domain.InvoiceGeneratePayment) (invoiceFeePair, error) {
	amount, ok := new(big.Rat).SetString(payment.Amount)
	if !ok {
		return invoiceFeePair{}, domain.ErrPaymentStateServer
	}
	if subject != 1 {
		zero, _ := floatFee(0)
		return invoiceFeePair{membership: zero, activity: decimalFee(payment.Amount)}, nil
	}
	activity := 0.0
	if payment.MetaPaymentCategories != nil {
		parsed, err := dto.ParseReferenceJSON([]byte(*payment.MetaPaymentCategories))
		if err != nil {
			return invoiceFeePair{}, err
		}
		if dto.PythonTruthy(parsed) {
			items, ok := parsed.([]any)
			if !ok {
				// Iterating a dict/str yields strings: item["amount"] is a TypeError.
				return invoiceFeePair{}, domain.ErrPaymentStateServer
			}
			for _, item := range items {
				object, ok := item.(domain.RequestObject)
				if !ok {
					return invoiceFeePair{}, domain.ErrPaymentStateServer
				}
				value, present := object.Lookup("amount")
				if !present {
					return invoiceFeePair{}, domain.ErrPaymentStateServer
				}
				if !dto.PythonTruthy(value) {
					continue
				}
				number, err := invoiceFloatOfStr(value)
				if err != nil {
					return invoiceFeePair{}, err
				}
				activity += number
			}
		}
	}
	activityFee, err := floatFeeAny(activity)
	if err != nil {
		return invoiceFeePair{}, err
	}
	if amount.Sign() <= 0 {
		return invoiceFeePair{membership: decimalFee(payment.Amount), activity: activityFee}, nil
	}
	base, err := strconv.ParseFloat(payment.Amount, 64)
	if err != nil {
		return invoiceFeePair{}, domain.ErrPaymentStateServer
	}
	membershipFee, err := floatFeeAny(base - activity)
	if err != nil {
		return invoiceFeePair{}, err
	}
	return invoiceFeePair{membership: membershipFee, activity: activityFee}, nil
}

// invoiceFloatOfStr is float(str(value)) of a stored JSON value: strings and
// numbers; str() of booleans, lists and dicts never parses (ValueError, 500).
func invoiceFloatOfStr(value any) (float64, error) {
	switch v := value.(type) {
	case string:
		number, ok := dto.PythonFloatParse(v)
		if !ok {
			return 0, domain.ErrPaymentStateServer
		}
		return number, nil
	case json.Number:
		// str() of a JSON int is its digits; of a JSON float the shortest repr
		// of the same double: float() of either is the parsed value.
		number, ok := dto.PythonFloatParse(string(v))
		if !ok {
			return 0, domain.ErrPaymentStateServer
		}
		return number, nil
	}
	return 0, domain.ErrPaymentStateServer
}

// --- generate_invoice_description ---------------------------------------------

var invoiceDescriptionTemplates = map[int]string{
	0: "Ricevuta di pagamento con causale <b>%s</b> per l'associazione <b>%s</b>.",
	1: "Ricevuta di pagamento per l'iscrizione %sdi <b>%s</b> all'associazione <b>%s</b>.",
	2: "Ricevuta di pagamento di <b>%s</b> per il corso <b>%s</b> dell'associazione <b>%s</b>.",
}

var djangoEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;")

// formatHTML is format_html: every argument conditional_escape'd.
func formatHTML(template string, args ...string) string {
	escaped := make([]any, len(args))
	for i, arg := range args {
		escaped[i] = djangoEscaper.Replace(arg)
	}
	return fmt.Sprintf(template, escaped...)
}

// fullName is get_full_name(): '%s %s' % (first, last) stripped.
func fullName(first, last *string) string {
	return dto.PythonStrip(pythonOptional(first) + " " + pythonOptional(last))
}

// invoicePeriod is get_period() of a subscription or membership.
func invoicePeriod(period domain.InvoiceGeneratePeriod) string {
	switch {
	case period.Start == nil && period.End == nil:
		return ""
	case period.Start == nil:
		return strconv.Itoa(period.End.Year())
	case period.End == nil || period.Start.Year() == period.End.Year():
		return strconv.Itoa(period.Start.Year())
	}
	short := func(year int) string {
		text := strconv.Itoa(year)
		if len(text) <= 2 {
			return ""
		}
		return text[2:]
	}
	return short(period.Start.Year()) + "/" + short(period.End.Year())
}

// metaName is (payment.meta or {}).get('subscription_data') or {} and its
// truthy 'name' as str(); ok false: nothing to add.
func metaName(meta *string, anyValue bool) (string, bool, error) {
	if meta == nil {
		return "", false, nil
	}
	parsed, err := dto.ParseReferenceJSON([]byte(*meta))
	if err != nil {
		return "", false, err
	}
	if !dto.PythonTruthy(parsed) {
		return "", false, nil
	}
	object, ok := parsed.(domain.RequestObject)
	if !ok {
		return "", false, domain.ErrPaymentStateServer
	}
	data, _ := object.Lookup("subscription_data")
	if !dto.PythonTruthy(data) {
		return "", false, nil
	}
	fields, ok := data.(domain.RequestObject)
	if !ok {
		return "", false, domain.ErrPaymentStateServer
	}
	name, _ := fields.Lookup("name")
	if !dto.PythonTruthy(name) {
		return "", false, nil
	}
	switch v := name.(type) {
	case string:
		return v, true, nil
	case bool:
		return "True", true, nil
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return string(v), true, nil
		}
	}
	if anyValue {
		return dto.PythonStr(name), true, nil
	}
	// str() of floats, lists and dicts: not ported.
	return "", false, domain.ErrPaymentStateNotPorted
}

func (s *InvoiceGenerations) description(ctx context.Context, tx domain.InvoiceGenerateTransaction, row *domain.PaymentStateRow,
	payment domain.InvoiceGeneratePayment, associate *string, association string, group *string) (string, error) {
	denominationValue, err := tx.AssociationDenomination(ctx, association)
	if err != nil {
		return "", err
	}
	denomination := pythonOptional(denominationValue)
	reason := "Pagamento"
	if row.Description != nil && *row.Description != "" {
		reason = *row.Description
	} else if row.CategoryID != nil && row.CategoryName != nil && *row.CategoryName != "" {
		reason = *row.CategoryName
	}
	if associate == nil {
		recipient := ""
		if row.SupplierID != nil {
			recipient = pythonOptionalEmpty(payment.SupplierName)
		} else if payment.HasInstructor {
			recipient = fullName(payment.InstructorFirstName, payment.InstructorLastName)
		}
		if recipient != "" {
			return formatHTML("Ricevuta di pagamento di <b>%s</b> con causale <b>%s</b> per l'associazione <b>%s</b>.", recipient, reason,
				denomination), nil
		}
		return formatHTML(invoiceDescriptionTemplates[0], reason, denomination), nil
	}
	template, ok := invoiceDescriptionTemplates[row.Subject]
	if !ok {
		// PAYMENT_INVOICE_DESCRIPTION[subject] is a KeyError.
		return "", domain.ErrPaymentStateServer
	}
	first, last, err := tx.AssociateNames(ctx, *associate)
	if err != nil {
		return "", err
	}
	name := fullName(first, last)
	switch row.Subject {
	case 1:
		subName, label := "", ""
		subscription, period, err := tx.SubscriptionByPayment(ctx, row.ID, group)
		if err != nil {
			return "", err
		}
		if subscription != nil {
			label = invoicePeriod(*period) + " "
		}
		metaLabel, present, err := metaName(row.Meta, s.anyMetaName)
		if err != nil {
			return "", err
		}
		if present {
			subName = label + "(" + metaLabel + ") "
		}
		if subscription != nil {
			membership, err := tx.FirstMembership(ctx, *subscription)
			if err != nil {
				return "", err
			}
			if membership != nil {
				subName += " e tesseramento, " + invoicePeriod(membership.InvoiceGeneratePeriod) + " (" + pythonOptional(membership.Type) + ") "
			}
		}
		return formatHTML(template, subName, name, denomination), nil
	case 0:
		category := reason
		if row.CategoryID != nil {
			category = pythonOptional(row.CategoryName)
		}
		return formatHTML(template, category, denomination), nil
	}
	title, err := tx.CourseTitle(ctx, row.ID, group)
	if err != nil {
		return "", err
	}
	if !title.Found {
		return djangoEscaper.Replace(reason), nil
	}
	if title.Missing {
		return "", domain.ErrPaymentStateServer
	}
	return formatHTML(template, name, pythonOptional(title.Title), denomination), nil
}

func pythonOptionalEmpty(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
