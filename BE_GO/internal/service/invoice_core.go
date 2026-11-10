package service

import (
	"context"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// InvoiceCore ports invoice_list, invoice_list_archived, invoice_bulk_archive,
// invoice_bulk_delete and invoice_delete (invoice_views.py).
type InvoiceCore struct {
	store     domain.InvoiceCoreStore
	publisher celery.Publisher
	now       func() time.Time
}

func NewInvoiceCore(store domain.InvoiceCoreStore, publisher celery.Publisher, now func() time.Time) *InvoiceCore {
	return &InvoiceCore{store: store, publisher: publisher, now: now}
}

// ErrInvoiceCoreForbidden is IsAssociationUser's denial (DRF's generic 403).
var ErrInvoiceCoreForbidden = errors.New("invoice core forbidden")

// InvoiceCoreRequest is one parsed request.
type InvoiceCoreRequest struct {
	Identity    domain.ResolvedIdentity
	Query       domain.RequestObject
	Body        any
	Form        bool
	GroupHeader string
	// Authorization is the request's Authorization header (nil when absent).
	Authorization *string
	Audit         domain.LoginAuditContext
}

// invoiceCoreModelUUID is the model UUIDField.to_python of a lookup value
// (uuid.UUID(hex=...)); a rejected value is a ValidationError (500).
func invoiceCoreModelUUID(raw string) (string, error) {
	ids, ok := dto.InvoiceCoreLookupIDs([]any{raw})
	if !ok || len(ids) != 1 {
		return "", domain.ErrInvoiceCoreServer
	}
	return ids[0], nil
}

// invoiceCoreFields are Invoice's concrete field names and attnames by column.
var invoiceCoreFields = map[string]string{"invoice_id": "invoice_id", "pk": "invoice_id", "document_pdf_id": "document_pdf_id",
	"sport_association_id": "sport_association_id", "creation_date": "creation_date", "membership_fee": "membership_fee",
	"activity_fee": "activity_fee", "meta_payment_categories": "meta_payment_categories", "description": "description", "number": "number",
	"archived": "archived", "cancelled": "cancelled", "selected_tutor_id": "selected_tutor_id", "meta": "meta",
	"imported_from_associami": "imported_from_associami", "group_id": "group_id", "deleted": "deleted"}

// invoiceCoreRelations are Invoice's relations: order_by() accepts them and
// paths through them, and Django resolves them only when compiling the rows.
var invoiceCoreRelations = map[string]bool{"document_pdf": true, "sport_association": true, "selected_tutor": true, "group": true,
	"attached_documents": true, "payment": true, "invoicerows": true}

// invoiceCoreOrder is order_by(f"{'-' if desc else ”}{field}"): one leading
// '-' is stripped, then the name must resolve (FieldError, 500); random,
// relations and transforms are refused (not ported).
func invoiceCoreOrder(filter *domain.InvoiceCoreFilter, field, direction string) error {
	if direction == "desc" {
		field = "-" + field
	}
	if field == "?" {
		return domain.ErrInvoiceCoreNotPorted
	}
	name, descending := strings.CutPrefix(field, "-")
	if column, ok := invoiceCoreFields[name]; ok {
		filter.Order = []domain.PaymentReadsOrder{{Column: column, Descending: descending}}
		return nil
	}
	first, _, _ := strings.Cut(name, "__")
	if _, ok := invoiceCoreFields[first]; ok || invoiceCoreRelations[first] {
		return domain.ErrInvoiceCoreNotPorted
	}
	return domain.ErrInvoiceCoreServer
}

// association is request.user.sportassociation, else the scoped live .get().
func (s *InvoiceCore) association(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	id, err := s.store.UserAssociation(ctx, identity.ScopeUser.ID)
	if err != nil || id != nil {
		if id == nil {
			return "", err
		}
		return *id, nil
	}
	return s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentReadsScope(identity))
}

// List follows invoice_list.
func (s *InvoiceCore) List(ctx context.Context, request InvoiceCoreRequest) (any, error) {
	query, identity := request.Query, request.Identity
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	if raw, _ := paymentReadsParam(query, "query[invoice_id]"); raw != "" {
		id, err := invoiceCoreModelUUID(raw)
		if err != nil {
			return nil, err
		}
		row, err := s.store.Invoice(ctx, id, group, scope)
		if err != nil {
			return nil, err
		}
		if row == nil {
			return nil, domain.ErrInvoiceCoreMissing
		}
		invoice, err := dto.InvoiceCoreInvoice(*row)
		if err != nil {
			return nil, err
		}
		return map[string]any{"data": map[string]any{"invoice": invoice}}, nil
	}
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	filter := domain.InvoiceCoreFilter{AssociationID: association, Scope: scope, Group: group}
	if raw, present := paymentReadsParam(query, "query[archived]"); present {
		if filter.Archived, err = paymentReadsBoolean(raw); err != nil {
			return nil, err
		}
	}
	day, month, err := s.store.BalanceStart(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	from, to, ok := balanceSheetRange(s.now(), day, month)
	if !ok {
		return nil, domain.ErrInvoiceCoreServer
	}
	switch raw, _ := paymentReadsParam(query, "query[current_year]"); raw {
	case "1":
		filter.Year = &domain.InvoiceCoreYear{Within: true, From: from, To: to}
	case "0":
		filter.Year = &domain.InvoiceCoreYear{From: from, To: to}
	}
	if raw, _ := paymentReadsParam(query, "query[generalSearch]"); raw != "" {
		if date, ok := dto.PythonStrptimeDMY(raw); ok {
			raw = date
		}
		if value, ok := dto.PythonFloatParse(strings.ReplaceAll(raw, ",", ".")); ok {
			switch {
			case math.IsNaN(value):
				// The DecimalField lookup raises ValidationError, which the view's except does not catch.
				return nil, domain.ErrInvoiceCoreServer
			case math.IsInf(value, 0):
				return nil, domain.ErrInvoiceCoreNotPorted
			}
			exact := dto.DecimalFromFloat9(value)
			filter.AmountText, filter.AmountExact = &raw, &exact
		} else {
			search, err := personaSearch(raw, s.now().UTC().Year()%100)
			if err != nil {
				return nil, domain.ErrInvoiceCoreNotPorted
			}
			filter.Smart = &domain.PaymentReadsSmartSearch{Words: search.Words, Dates: search.Dates, Permutations: search.Permutations}
		}
	}
	if field, _ := paymentReadsParam(query, "sort[field]"); field != "" {
		direction, _ := paymentReadsParam(query, "sort[sort]")
		if err := invoiceCoreOrder(&filter, field, direction); err != nil {
			return nil, err
		}
	}
	count, err := s.store.CountInvoices(ctx, filter)
	if err != nil {
		return nil, err
	}
	number, size, pages, limit, offset, err := paymentReadsPage(count, query)
	if err != nil {
		return nil, err
	}
	ids := []string{}
	if limit > 0 {
		if ids, err = s.store.InvoiceRows(ctx, filter, limit, offset); err != nil {
			return nil, err
		}
	}
	distinct := []string{}
	seen := map[string]bool{}
	for _, id := range ids {
		if !seen[id] {
			seen[id] = true
			distinct = append(distinct, id)
		}
	}
	invoices, err := s.store.Invoices(ctx, distinct)
	if err != nil {
		return nil, err
	}
	first, err := s.store.FirstPayments(ctx, distinct, group, scope, false)
	if err != nil {
		return nil, err
	}
	paymentIDs := []string{}
	for _, id := range distinct {
		if payment, ok := first[id]; ok {
			paymentIDs = append(paymentIDs, payment)
		}
	}
	payments, err := s.store.ListPayments(ctx, paymentIDs, group)
	if err != nil {
		return nil, err
	}
	data := make(map[string]any, len(ids))
	for index, id := range ids {
		item, err := dto.InvoiceCoreInvoice(invoices[id])
		if err != nil {
			return nil, err
		}
		if payment, ok := first[id]; ok {
			item["payment"], item["no_payment"] = dto.InvoiceCoreListPayment(payments[payment]), false
		} else {
			item["payment"], item["no_payment"] = item["meta"], true
		}
		delete(item, "meta")
		data[strconv.Itoa(index)] = item
	}
	// After the loop: print_document_invoice.delay(invoice_id, auth_token)
	// once per listed row without a PDF, in page order.
	for _, id := range ids {
		if invoices[id].DocumentID != nil {
			continue
		}
		if err := invoiceCorePrint(ctx, s.publisher, id, authorizationArg(request.Authorization)); err != nil {
			return nil, err
		}
	}
	return map[string]any{"data": data, "meta": paymentReadsMeta(count, number, size, pages, ids)}, nil
}

// invoiceCorePrint publishes print_document_invoice with tuple args (.delay).
func invoiceCorePrint(ctx context.Context, publisher celery.Publisher, args ...any) error {
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), invoicePublishTimeout)
	defer cancel()
	_, err := publisher.Publish(publish, celery.Task{Name: "print_document_invoice", Args: args})
	return err
}

// Archived follows invoice_list_archived (IsAssociationUser runs before the view).
func (s *InvoiceCore) Archived(ctx context.Context, request InvoiceCoreRequest) (any, error) {
	identity := request.Identity
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return nil, ErrInvoiceCoreForbidden
	}
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	association, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return nil, err
	}
	invoices, err := s.store.ArchivedInvoices(ctx, association, group, scope)
	if err != nil {
		return nil, err
	}
	ids := make([]string, len(invoices))
	for i, row := range invoices {
		ids[i] = row.ID
	}
	first, err := s.store.FirstPayments(ctx, ids, group, scope, true)
	if err != nil {
		return nil, err
	}
	paymentIDs := []string{}
	for _, id := range ids {
		if payment, ok := first[id]; ok {
			paymentIDs = append(paymentIDs, payment)
		}
	}
	payments, err := s.store.ArchivedPayments(ctx, paymentIDs, group)
	if err != nil {
		return nil, err
	}
	data := make([]any, 0, len(invoices))
	for _, row := range invoices {
		item, err := dto.InvoiceCoreInvoice(row)
		if err != nil {
			return nil, err
		}
		if payment, ok := first[row.ID]; ok {
			if item["payment"], err = dto.InvoiceCoreArchivedPayment(payments[payment]); err != nil {
				return nil, err
			}
		}
		data = append(data, item)
	}
	return map[string]any{"data": data}, nil
}

// invoiceCoreIDs reads data['invoice_ids']: request.data must have the key
// (a list, scalar or null body has no .keys(): 500). A form body is a
// QueryDict whose item is the last value; file parts are not ported.
func invoiceCoreIDs(request InvoiceCoreRequest) (any, error) {
	object, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrInvoiceCoreServer
	}
	var value any
	found := false
	for _, field := range object {
		if field.Name == "invoice_ids" {
			value, found = field.Value, true
		}
	}
	if !found {
		return nil, domain.ErrInvoiceCoreServer
	}
	if _, upload := value.(domain.RequestUpload); upload {
		return nil, domain.ErrInvoiceCoreNotPorted
	}
	return value, nil
}

// bulk runs the shared head of invoice_bulk_archive and invoice_bulk_delete:
// the key check, the scoped live association .get(), then the lookup values
// (rejected values raise when the filter is built, before any write).
func (s *InvoiceCore) bulk(ctx context.Context, request InvoiceCoreRequest, apply func(domain.InvoiceCoreTransaction, []string) error) error {
	raw, err := invoiceCoreIDs(request)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginInvoiceCore(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	identity := request.Identity
	scope := paymentCategoryAssociationScope(identity)
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return err
	}
	ids, ok := dto.InvoiceCoreLookupIDs(raw)
	if !ok {
		return domain.ErrInvoiceCoreServer
	}
	matched, err := tx.MatchingInvoices(ctx, association, ids, supplierGroup(request.GroupHeader), scope)
	if err != nil {
		return err
	}
	if err = apply(tx, matched); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *InvoiceCore) deleteContext(request InvoiceCoreRequest) domain.InvoiceCoreDeleteContext {
	return domain.InvoiceCoreDeleteContext{Audit: paymentCategoryAuditContext(request.Identity, request.Audit),
		At: s.now().UTC().Truncate(time.Microsecond)}
}

// BulkArchive follows invoice_bulk_archive: bulk_update(archived=True), no audit.
func (s *InvoiceCore) BulkArchive(ctx context.Context, request InvoiceCoreRequest) error {
	return s.bulk(ctx, request, func(tx domain.InvoiceCoreTransaction, ids []string) error {
		return tx.ArchiveInvoices(ctx, ids)
	})
}

// BulkDelete follows invoice_bulk_delete: QuerySet.delete().
func (s *InvoiceCore) BulkDelete(ctx context.Context, request InvoiceCoreRequest) error {
	return s.bulk(ctx, request, func(tx domain.InvoiceCoreTransaction, ids []string) error {
		return tx.DeleteInvoices(ctx, ids, s.deleteContext(request))
	})
}

// Delete follows invoice_delete: is_valid_uuid, the scoped live association
// .get(), the association's live, group-aware invoice (none: 500), its first
// payment by key unlinked, unpaid and saved (Payment.save() defaults,
// audited), then invoice.delete().
func (s *InvoiceCore) Delete(ctx context.Context, request InvoiceCoreRequest, raw string) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginInvoiceCore(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	identity := request.Identity
	scope, group := paymentCategoryAssociationScope(identity), supplierGroup(request.GroupHeader)
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return err
	}
	matched, err := tx.MatchingInvoices(ctx, association, []string{id}, group, scope)
	if err != nil {
		return err
	}
	if len(matched) == 0 {
		return domain.ErrInvoiceCoreServer // raise Exception('Invoice not found')
	}
	audit := s.deleteContext(request)
	payment, err := tx.PaymentToUnlink(ctx, id, group, scope)
	if err != nil {
		return err
	}
	if payment != nil {
		changes := map[string][2]string{"invoice": {id, "None"}}
		if payment.Paid {
			changes["paid"] = [2]string{"True", "False"}
		}
		if err = paymentSaveDefaults(ctx, tx, &payment.PaymentSaveRow, changes); err != nil {
			return err
		}
		if err = tx.UnlinkPayment(ctx, *payment); err != nil {
			return err
		}
		if err = tx.AppendPaymentSave(ctx, *payment, changes, audit); err != nil {
			return err
		}
	}
	if err = tx.DeleteInvoices(ctx, matched, audit); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
