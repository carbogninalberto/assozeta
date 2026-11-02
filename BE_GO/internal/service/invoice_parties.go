package service

import (
	"context"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// InvoiceParties ports invoice_customers_{list,add,update} and
// invoice_suppliers_{list,add,update}.
type InvoiceParties struct {
	store domain.InvoicePartyStore
	now   func() time.Time
	ids   func() string
}

func NewInvoiceParties(store domain.InvoicePartyStore, now func() time.Time, ids func() string) *InvoiceParties {
	return &InvoiceParties{store: store, now: now, ids: ids}
}

// InvoicePartyRequest carries what the views read besides the body.
type InvoicePartyRequest struct {
	Identity domain.ResolvedIdentity
	Body     any
	Form     bool
	Query    domain.RequestObject
	Group    string
	Audit    domain.LoginAuditContext
}

func invoicePartyStr(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

func invoicePartyNaive(value *time.Time) string {
	if value == nil {
		return "None"
	}
	moment := value.UTC()
	if moment.Nanosecond()/1000 == 0 {
		return moment.Format("2006-01-02 15:04:05")
	}
	return moment.Format("2006-01-02 15:04:05.000000")
}

func invoicePartyOptional(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// userAssociation is request.user.sport_association: athletes raise
// PermissionDenied, otherwise SportAssociation.objects.get(user=...).
func (s *InvoiceParties) userAssociation(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", domain.ErrInvoicePartyNotAssociation
	}
	return s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
}

func (s *InvoiceParties) scopedAssociation(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	association, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return "", err
	}
	if association == nil {
		return "", domain.ErrInvoicePartyMissing
	}
	return *association, nil
}

// CustomerList follows invoice_customers_list. Its first loop generates XML
// and runs the print task for every invoice without a PDF: not ported, so any
// such invoice refuses the request before anything else.
func (s *InvoiceParties) CustomerList(ctx context.Context, request InvoicePartyRequest) (map[string]any, error) {
	pending, err := s.store.CustomerInvoicesWithoutPDF(ctx)
	if err != nil {
		return nil, err
	}
	if pending {
		return nil, domain.ErrInvoicePartyNotPorted
	}
	association, err := s.userAssociation(ctx, request.Identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.CustomerInvoices(ctx, association)
	if err != nil {
		return nil, err
	}
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		items = append(items, dto.CustomerInvoiceItem(row))
	}
	return map[string]any{"data": items}, nil
}

// requestObject is the view writing into request.data: a QueryDict (form)
// is immutable and any non-dict raises TypeError.
func invoicePartyObject(request InvoicePartyRequest) (domain.RequestObject, error) {
	object, ok := request.Body.(domain.RequestObject)
	if request.Form || !ok {
		return nil, domain.ErrInvoicePartyServer
	}
	return object, nil
}

// related resolves a PrimaryKeyRelatedField: a missing row is does_not_exist.
func (s *InvoiceParties) related(ctx context.Context, errors dto.InvoicePartyErrors, name, table, key string, value dto.InvoicePartyRelated) error {
	if value.ID == nil {
		return nil
	}
	exists, err := s.store.Exists(ctx, table, key, *value.ID)
	if err != nil {
		return err
	}
	if !exists {
		errors[name] = []string{"related-does-not-exist:" + value.Raw}
	}
	return nil
}

func (s *InvoiceParties) unique(ctx context.Context, errors dto.InvoicePartyErrors, name, table, key, message string, id *string, exclude string) error {
	if id == nil || *id == exclude {
		return nil
	}
	exists, err := s.store.Exists(ctx, table, key, *id)
	if err != nil {
		return err
	}
	if exists {
		existing, _ := errors[name].([]string)
		errors[name] = append(existing, "unique:"+message)
	}
	return nil
}

func invoicePartyGroup(header string) *string { return supplierGroup(header) }

// CustomerAdd follows invoice_customers_add up to the serializer's validation;
// a valid invoice would be saved and turned into XML and a PDF (not ported).
func (s *InvoiceParties) CustomerAdd(ctx context.Context, request InvoicePartyRequest) error {
	association, err := s.userAssociation(ctx, request.Identity)
	if err != nil {
		return err
	}
	object, err := invoicePartyObject(request)
	if err != nil {
		return err
	}
	// Missing or null dates default to today; both go through
	// strptime('%Y-%m-%d') (TypeError/ValueError: 500).
	for _, name := range []string{"transmitting_date", "payment_expiry_date"} {
		value, present := object.Lookup(name)
		if !present || value == nil {
			continue
		}
		text, ok := value.(string)
		if !ok {
			return domain.ErrInvoicePartyServer
		}
		if _, ok = dto.PythonStrptimeDate(text); !ok {
			return domain.ErrInvoicePartyServer
		}
	}
	input, errors := dto.ParseCustomerInvoiceAdd(object)
	group := invoicePartyGroup(request.Group)
	if err = s.unique(ctx, errors, "customer_invoice_id", "application_customerinvoice", "customer_invoice_id",
		"customer invoice:customer invoice id", input.ID, ""); err != nil {
		return err
	}
	if err = s.related(ctx, errors, "pdf", "docmanager_document", "document_id", input.PDF); err != nil {
		return err
	}
	if input.Payment.ID != nil {
		found, owner, err := s.store.PaymentAssociation(ctx, *input.Payment.ID, group)
		if err != nil {
			return err
		}
		if !found {
			errors["payment"] = []string{"related-does-not-exist:" + input.Payment.Raw}
		} else if owner != nil && *owner != association {
			return domain.ErrInvoicePartyForeignReference
		}
	}
	if len(errors) > 0 {
		return &dto.InvoicePartyValidationError{Errors: errors}
	}
	if input.Prefix != nil && input.Number != nil && input.FiscalYear != nil {
		taken, err := s.store.CustomerInvoiceTaken(ctx, *input.Prefix, *input.Number, *input.FiscalYear, association)
		if err != nil {
			return err
		}
		if taken {
			return &dto.InvoicePartyValidationError{Errors: dto.InvoicePartyErrors{"non_field_errors": []string{dto.InvoicePartyUniqueTogether}}}
		}
	}
	if (!input.IDTransmitterSet || input.IDTransmitter == nil) && (!input.ContactEmailSet || input.ContactEmail == nil) {
		return &dto.InvoicePartyValidationError{Errors: dto.InvoicePartyErrors{"non_field_errors": []string{
			dto.InvoicePartyText + "Either transferor_contact_email or id_transmitter must be provided!"}}}
	}
	taken, err := s.store.CustomerNumberTaken(ctx, input.Number, input.FiscalYear)
	if err != nil {
		return err
	}
	if taken {
		return &dto.InvoicePartyValidationError{Errors: dto.InvoicePartyErrors{"non_field_errors": []string{
			dto.InvoicePartyText + "Invoice already exists!"}}}
	}
	return domain.ErrInvoicePartyNotPorted
}

// CustomerUpdate follows invoice_customers_update: paid and transmitted are
// assigned raw and Model.save() runs; auditlog's pre_save entry is written
// before BooleanField.to_python or NOT NULL can reject a value.
func (s *InvoiceParties) CustomerUpdate(ctx context.Context, request InvoicePartyRequest, raw string) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	association, err := s.scopedAssociation(ctx, request.Identity)
	if err != nil {
		return err
	}
	invoice, err := s.store.CustomerInvoice(ctx, id, association)
	if err != nil {
		return err
	}
	if invoice == nil {
		return domain.ErrInvoicePartyMissing
	}
	values := map[string]any{}
	switch body := request.Body.(type) {
	case domain.RequestObject:
		for _, name := range []string{"paid", "transmitted"} {
			if value, present := body.Lookup(name); present {
				if _, upload := value.(domain.RequestUpload); upload {
					return domain.ErrInvoicePartyNotPorted
				}
				values[name] = value
			}
		}
	case []any:
		// 'paid' in data tests list membership; data['paid'] is then a TypeError.
		for _, item := range body {
			if item == "paid" || item == "transmitted" {
				return domain.ErrInvoicePartyServer
			}
		}
	case string:
		if strings.Contains(body, "paid") || strings.Contains(body, "transmitted") {
			return domain.ErrInvoicePartyServer
		}
	default:
		return domain.ErrInvoicePartyServer
	}
	stored := map[string]bool{"paid": invoice.Paid, "transmitted": invoice.Transmitted}
	changes := map[string][2]string{}
	for _, name := range []string{"paid", "transmitted"} {
		if value, set := values[name]; set {
			if text := dto.PythonAuditStr(value); text != invoicePartyStr(stored[name]) {
				changes[name] = [2]string{invoicePartyStr(stored[name]), text}
			}
		}
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	auditContext := paymentCategoryAuditContext(request.Identity, request.Audit)
	if len(changes) > 0 {
		direct := "direct"
		if err = s.store.AppendAudit(ctx, domain.InvoicePartyAudit{Model: "customerinvoice", Key: id, Action: 1, Changes: changes,
			AssociationID: &invoice.AssociationID, ResolutionPath: &direct}, auditContext, at); err != nil {
			return err
		}
	}
	result := map[string]bool{}
	for _, name := range []string{"paid", "transmitted"} {
		result[name] = stored[name]
		if value, set := values[name]; set {
			converted, ok := dto.DjangoBooleanToPython(value)
			if !ok || converted == nil {
				return domain.ErrInvoicePartyServer
			}
			result[name] = *converted
		}
	}
	return s.store.UpdateCustomerInvoice(ctx, id, result["paid"], result["transmitted"])
}

// supplierInvoiceOrder maps InvoiceSuppliers field names and attnames to columns.
var supplierInvoiceOrder = map[string]string{
	"invoice_supplier_id": "invoice_supplier_id", "pk": "invoice_supplier_id", "invoice_identifier": "invoice_identifier",
	"amount": "amount", "payment_date": "payment_date", "expire_date": "expire_date", "paid": "paid", "notes": "notes",
	"sport_association": "sport_association_id", "sport_association_id": "sport_association_id", "payment": "payment_id",
	"payment_id": "payment_id", "document_pdf": "document_pdf_id", "document_pdf_id": "document_pdf_id", "supplier": "supplier_id",
	"supplier_id": "supplier_id",
}

// invoicePartyRenderable refuses payments whose nested renderings are not
// ported (associate, instructor, invoice, subscription and course lookups).
func invoicePartyRenderable(payment *domain.InvoicePartyPayment) bool {
	return payment == nil || payment.AssociateID == nil && payment.InstructorID == nil && payment.InvoiceID == nil &&
		(payment.Subject == 0 || payment.Subject == 3)
}

// SupplierList follows invoice_suppliers_list.
func (s *InvoiceParties) SupplierList(ctx context.Context, request InvoicePartyRequest) (map[string]any, error) {
	association, err := s.scopedAssociation(ctx, request.Identity)
	if err != nil {
		return nil, err
	}
	query := domain.SupplierInvoiceQuery{AssociationID: association, OrderColumn: "expire_date", Descending: true}
	if search := supplierText(request.Query, "query[generalSearch]"); search != "" {
		if date, ok := dto.PythonStrptimeDMY(search); ok {
			search = date
		}
		// float() then the amount filter; a non-finite value makes the
		// DecimalField lookup raise, which the view's except also catches.
		if value, ok := dto.PythonFloatParse(strings.ReplaceAll(search, ",", ".")); ok && !math.IsInf(value, 0) && !math.IsNaN(value) {
			text, exact := dto.PythonFloatStr(value), dto.DecimalFromFloat9(value)
			query.AmountText, query.AmountExact = &text, &exact
		} else {
			query.Text = &search
		}
	}
	if field := supplierText(request.Query, "sort[field]"); field != "" {
		if supplierText(request.Query, "sort[sort]") == "desc" {
			field = "-" + field
		}
		name, descending := strings.CutPrefix(field, "-")
		column, ok := supplierInvoiceOrder[name]
		if !ok {
			// Unknown names and relation paths ("supplier__name", "?") are
			// refused; Django raises FieldError for the former.
			return nil, domain.ErrInvoicePartyOrdering
		}
		query.OrderColumn, query.Descending = column, descending
	}
	count, err := s.store.CountSupplierInvoices(ctx, query)
	if err != nil {
		return nil, err
	}
	size, present := supplierParam(request.Query, "pagination[perpage]")
	number, numberPresent := supplierParam(request.Query, "pagination[page]")
	page, err := dto.DRFPageNumber(number, numberPresent, count, dto.DRFPageSize(size, present, 10))
	if err != nil {
		return nil, err
	}
	rows, err := s.store.SupplierInvoices(ctx, query, page.Size, page.Offset(), invoicePartyGroup(request.Group))
	if err != nil {
		return nil, err
	}
	data, ids := map[string]any{}, make([]string, 0, len(rows))
	for index, row := range rows {
		if !invoicePartyRenderable(row.Payment) {
			return nil, domain.ErrInvoicePartyNotPorted
		}
		data[strconv.Itoa(index)] = dto.SupplierInvoiceItem(row)
		ids = append(ids, row.ID)
	}
	return map[string]any{"data": data, "meta": map[string]any{"total": page.Count, "page": page.Number, "pages": page.Pages,
		"perpage": page.Size, "rowIds": ids}}, nil
}

// supplierRelations checks InvoiceSuppliersSerializer's relations: the
// invoice id's UniqueValidator, the tenant-scoped association (another
// tenant's is the generic 403, escaping the field errors) and the document.
func (s *InvoiceParties) supplierRelations(ctx context.Context, input dto.SupplierInvoiceInput, errors dto.InvoicePartyErrors,
	association, exclude string) error {
	if err := s.unique(ctx, errors, "invoice_supplier_id", "application_invoicesuppliers", "invoice_supplier_id",
		"invoice suppliers:invoice supplier id", input.ID, exclude); err != nil {
		return err
	}
	if input.Association.ID != nil {
		exists, err := s.store.Exists(ctx, "application_sportassociation", "sport_association_id", *input.Association.ID)
		if err != nil {
			return err
		}
		if !exists {
			errors["sport_association"] = []string{"related-does-not-exist:" + input.Association.Raw}
		} else if *input.Association.ID != association {
			return domain.ErrInvoicePartyForeignReference
		}
	}
	return s.related(ctx, errors, "document_pdf", "docmanager_document", "document_id", input.Document)
}

func invoicePartyRespond(status int, message string) error {
	return &domain.InvoicePartyResponseError{Status: status, Message: message}
}

// SupplierAdd follows invoice_suppliers_add: lookups, PaymentEntrySerializer
// and InvoiceSuppliersSerializer validation, then the payment (create and
// save) and, separately, the invoice (create and save): a failing invoice
// insert keeps the payment, as Django's autocommit does.
func (s *InvoiceParties) SupplierAdd(ctx context.Context, request InvoicePartyRequest) error {
	identity := request.Identity
	association, err := s.userAssociation(ctx, identity)
	if err != nil {
		return err
	}
	object, err := invoicePartyObject(request)
	if err != nil {
		return err
	}
	group := invoicePartyGroup(request.Group)
	lookup := func(name string) (any, error) {
		value, present := object.Lookup(name)
		if !present {
			return nil, domain.ErrInvoicePartyServer // KeyError
		}
		return value, nil
	}
	rawAccount, err := lookup("custom_accounts")
	if err != nil {
		return err
	}
	var account *string
	if rawAccount == nil {
		account, err = s.store.BankAccount(ctx, association, nil)
	} else {
		id, ok := dto.DjangoUUIDLookup(rawAccount)
		if !ok {
			return domain.ErrInvoicePartyServer
		}
		account, err = s.store.BankAccount(ctx, association, &id)
	}
	if err != nil {
		return err
	}
	if account == nil {
		return invoicePartyRespond(400, "Nessun conto bancario attivo trovato.")
	}
	rawSupplier, err := lookup("supplier_id")
	if err != nil {
		return err
	}
	supplierID, ok := dto.DjangoUUIDLookup(rawSupplier)
	if !ok {
		return domain.ErrInvoicePartyServer
	}
	supplier, err := s.store.LiveSupplier(ctx, supplierID, association, group)
	if err != nil {
		return err
	}
	if supplier == nil {
		return invoicePartyRespond(400, "Fornitore non trovato.")
	}
	paymentDate, err := lookup("payment_date")
	if err != nil {
		return err
	}
	now := s.now().UTC().Truncate(time.Microsecond)
	if paymentDate == nil {
		// timezone.now().isoformat()
		paymentDate = now.Format("2006-01-02T15:04:05.000000-07:00")
	}
	category, err := s.store.SupplierPaymentCategory(ctx, association)
	if err != nil {
		return err
	}
	if category == nil {
		return invoicePartyRespond(400, "Causale Pagamento Fornitore non disponibile.")
	}
	amount, err := lookup("amount")
	if err != nil {
		return err
	}
	paid, err := lookup("paid")
	if err != nil {
		return err
	}
	entry, errors := dto.ParseSupplierPaymentEntry(amount, paid, paymentDate)
	if len(errors) > 0 {
		return &dto.InvoicePartyValidationError{Errors: errors}
	}
	data := make(domain.RequestObject, 0, len(object)+1)
	for _, field := range object {
		switch field.Name {
		case "sport_association":
			continue
		case "payment_date":
			field.Value = paymentDate
		}
		data = append(data, field)
	}
	data = append(data, domain.RequestField{Name: "sport_association", Value: association})
	input, errors := dto.ParseSupplierInvoice(data, false, false)
	if err = s.supplierRelations(ctx, input, errors, association, ""); err != nil {
		return err
	}
	if len(errors) > 0 {
		return &dto.InvoicePartyValidationError{Errors: errors}
	}
	if input.NotPorted {
		return domain.ErrInvoicePartyNotPorted
	}
	var paymentGroup *string
	if request.Group != "" {
		// GroupAwareManager.create stores the raw header (rejected: 500).
		parsed, err := paymentInfoParseUUID(request.Group)
		if err != nil {
			return domain.ErrInvoicePartyServer
		}
		text := parsed.String()
		paymentGroup = &text
	}
	auditContext := paymentCategoryAuditContext(identity, request.Audit)
	payment := domain.NewSupplierPayment{ID: s.ids(), Amount: entry.Amount, CategoryID: *category, AccountID: *account,
		CreationDate: entry.CreationDate, PaymentDate: entry.PaymentDate, Paid: entry.Paid, GroupID: paymentGroup,
		Description: "Fattura fornitore (" + supplier.Name + ") generata automaticamente dal sistema in data " +
			now.Format("02/01/2006 alle 15:04") + "."}
	// The create resolves no association on the row and falls back to the
	// actor's; the save's pre_save entry reads the stored row (no association
	// yet) and resolves through the changes.
	actorAssociation, err := s.store.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	created := domain.InvoicePartyAudit{Model: "payment", Key: payment.ID, Action: 0}
	if actorAssociation != nil {
		path := "actor.sport_association"
		created.AssociationID, created.ResolutionPath = actorAssociation, &path
	}
	changesPath, direct := "changes_json.sport_association", "direct"
	owner := identity.ScopeUser.ID
	saved := domain.InvoicePartyAudit{Model: "payment", Key: payment.ID, Action: 1, Changes: map[string][2]string{
		"user": {"None", owner}, "sport_association": {"None", association}, "supplier": {"None", supplier.ID}},
		AssociationID: &association, ResolutionPath: &changesPath}
	if err = s.store.CreateSupplierPayment(ctx, payment, created, saved, owner, association, supplier.ID, auditContext, now); err != nil {
		return err
	}
	invoice := domain.NewSupplierInvoice{ID: s.ids(), Identifier: input.Identifier, Amount: input.Amount, AssociationID: association,
		PaymentDate: input.PaymentDate, ExpireDate: input.ExpireDate, Paid: input.Paid, Notes: input.Notes}
	if input.ID != nil {
		invoice.ID = *input.ID
	}
	invoice.DocumentID = input.Document.ID
	linked := domain.InvoicePartyAudit{Model: "invoicesuppliers", Key: invoice.ID, Action: 1, Changes: map[string][2]string{
		"payment": {"None", payment.ID}, "supplier": {"None", supplier.ID}}, AssociationID: &association, ResolutionPath: &direct}
	return s.store.CreateSupplierInvoice(ctx, invoice, domain.InvoicePartyAudit{Model: "invoicesuppliers", Key: invoice.ID, Action: 0,
		AssociationID: &association, ResolutionPath: &direct}, linked, payment.ID, supplier.ID, auditContext, now)
}

// SupplierUpdate follows invoice_suppliers_update: the partial serializer
// and the invoice save (its pre_save entry first), then the linked payment
// gets the raw paid value and the current time.
func (s *InvoiceParties) SupplierUpdate(ctx context.Context, request InvoicePartyRequest, raw string) error {
	identity := request.Identity
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	association, err := s.scopedAssociation(ctx, identity)
	if err != nil {
		return err
	}
	row, err := s.store.SupplierInvoice(ctx, id, association)
	if err != nil {
		return err
	}
	if row == nil {
		return domain.ErrInvoicePartyMissing
	}
	input, errors := dto.ParseSupplierInvoice(request.Body, true, request.Form)
	if _, isObject := request.Body.(domain.RequestObject); isObject {
		if err = s.supplierRelations(ctx, input, errors, association, row.ID); err != nil {
			return err
		}
	}
	if len(errors) > 0 {
		return &dto.InvoicePartyValidationError{Errors: errors}
	}
	if input.NotPorted || input.ID != nil && *input.ID != row.ID {
		// Nested writes, a payment_id to store, or a new primary key (save()
		// would insert a copy) are not ported.
		return domain.ErrInvoicePartyNotPorted
	}
	updated := *row
	changes := map[string][2]string{}
	text := func(name, old, new string) {
		if old != new {
			changes[name] = [2]string{old, new}
		}
	}
	if input.IdentifierSet {
		text("invoice_identifier", row.Identifier, input.Identifier)
		updated.Identifier = input.Identifier
	}
	if input.AmountSet {
		text("amount", row.Amount, input.Amount)
		updated.Amount = input.Amount
	}
	if input.PaymentDateSet {
		text("payment_date", invoicePartyNaive(row.PaymentDate), invoicePartyNaive(input.PaymentDate))
		updated.PaymentDate = input.PaymentDate
	}
	if input.ExpireDateSet {
		text("expire_date", invoicePartyNaive(row.ExpireDate), invoicePartyNaive(input.ExpireDate))
		updated.ExpireDate = input.ExpireDate
	}
	if input.PaidSet {
		text("paid", invoicePartyStr(row.Paid), invoicePartyStr(input.Paid))
		updated.Paid = input.Paid
	}
	if input.NotesSet {
		text("notes", invoicePartyOptional(row.Notes), invoicePartyOptional(input.Notes))
		updated.Notes = input.Notes
	}
	if input.Document.Present {
		text("document_pdf", invoicePartyOptional(row.DocumentID), invoicePartyOptional(input.Document.ID))
		updated.DocumentID = input.Document.ID
	}
	if input.PaymentNull || input.PaymentIDSet {
		text("payment", invoicePartyOptional(row.PaymentID), "None")
		updated.PaymentID = nil
	}
	group := invoicePartyGroup(request.Group)
	var payment *domain.InvoicePartyPayment
	var paid any
	paidPresent := false
	if object, ok := request.Body.(domain.RequestObject); ok {
		paid, paidPresent = object.Lookup("paid")
	}
	if updated.PaymentID != nil && paidPresent {
		var visible bool
		if payment, visible, err = s.store.Payment(ctx, *updated.PaymentID, group); err != nil {
			return err
		}
		_, upload := paid.(domain.RequestUpload)
		// Payment.save() derives a description for None or "quota corso", and
		// auditlog diffs against None for a payment the manager cannot load.
		if payment == nil || !visible || upload || payment.Description == nil || strings.ToLower(*payment.Description) == "quota corso" {
			return domain.ErrInvoicePartyNotPorted
		}
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	auditContext := paymentCategoryAuditContext(identity, request.Audit)
	direct := "direct"
	if len(changes) > 0 {
		if err = s.store.AppendAudit(ctx, domain.InvoicePartyAudit{Model: "invoicesuppliers", Key: row.ID, Action: 1, Changes: changes,
			AssociationID: &row.AssociationID, ResolutionPath: &direct}, auditContext, at); err != nil {
			return err
		}
	}
	if err = s.store.UpdateSupplierInvoice(ctx, updated); err != nil {
		return err
	}
	if updated.PaymentID == nil {
		return nil
	}
	if !paidPresent {
		return domain.ErrInvoicePartyServer // data['paid']: KeyError after the invoice save
	}
	category := payment.CategoryID
	paymentChanges := map[string][2]string{}
	if text := dto.PythonAuditStr(paid); text != invoicePartyStr(payment.Paid) {
		paymentChanges["paid"] = [2]string{invoicePartyStr(payment.Paid), text}
	}
	if old, new := invoicePartyNaive(payment.PaymentDate), invoicePartyNaive(&at); old != new {
		paymentChanges["payment_date"] = [2]string{old, new}
	}
	if category == nil {
		if category, err = s.store.DefaultPaymentCategory(ctx); err != nil {
			return err
		}
		if category != nil {
			paymentChanges["payment_category"] = [2]string{"None", *category}
		}
	}
	entry := domain.InvoicePartyAudit{Model: "payment", Key: payment.ID, Action: 1, Changes: paymentChanges}
	if payment.AssociationID != nil {
		entry.AssociationID, entry.ResolutionPath = payment.AssociationID, &direct
	} else if actor, err := s.store.ActorAssociation(ctx, identity.Actor.ID); err != nil {
		return err
	} else if actor != nil {
		path := "actor.sport_association"
		entry.AssociationID, entry.ResolutionPath = actor, &path
	}
	if len(paymentChanges) > 0 {
		if err = s.store.AppendAudit(ctx, entry, auditContext, at); err != nil {
			return err
		}
	}
	converted, ok := dto.DjangoBooleanToPython(paid)
	if !ok || converted == nil {
		return domain.ErrInvoicePartyServer
	}
	return s.store.UpdatePaymentPaid(ctx, payment.ID, *converted, at, category)
}
