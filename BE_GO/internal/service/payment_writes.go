package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// PaymentWrites ports payment_add, payment_update and payment_bulk_add.
type PaymentWrites struct {
	store   domain.PaymentWritesStore
	now     func() time.Time
	newID   func() string
	printer *InvoiceSyncPrint
	// documents stores attachments_data files (default_storage.save).
	documents *DocumentUploads
}

// WithDocuments sets the storage of attachments_data files.
func (s *PaymentWrites) WithDocuments(documents *DocumentUploads) *PaymentWrites {
	s.documents = documents
	return s
}

// WithInvoicePrint sets print_document_invoice's eager runner (the update of
// an invoiced payment prints the invoice after the save).
func (s *PaymentWrites) WithInvoicePrint(printer *InvoiceSyncPrint) *PaymentWrites {
	s.printer = printer
	return s
}

func NewPaymentWrites(store domain.PaymentWritesStore, now func() time.Time, newID func() string) *PaymentWrites {
	return &PaymentWrites{store: store, now: now, newID: newID}
}

// PaymentWritesRequest carries the request values the views read besides the body.
type PaymentWritesRequest struct {
	ID          string
	GroupHeader string
	Form        bool
	Audit       domain.LoginAuditContext
	// Authorization is the raw Authorization header (nil when absent).
	Authorization *string
}

func (s *PaymentWrites) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

func (s *PaymentWrites) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.PaymentWritesTransaction, string, error) {
	tx, err := s.store.BeginPaymentWrites(ctx)
	if err != nil {
		return nil, "", err
	}
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentStateScope(identity))
	if errors.Is(err, domain.ErrAssociationNotFound) {
		err = domain.ErrPaymentWritesServer
	}
	if err != nil {
		tx.Rollback()
		return nil, "", err
	}
	return tx, association, nil
}

// paymentWritesStrptime is datetime.strptime(text, '%Y-%m-%d'): ok false is
// the ValueError the views catch. strptime's \d also matches decimal digits
// of other scripts (paymentStateDateAny).
func paymentWritesStrptime(text string) (time.Time, bool, error) {
	moment, err := paymentStateDateAny(text)
	if err != nil {
		return time.Time{}, false, nil
	}
	return *moment, true, nil
}

// addDate is the add view's strptime attempt: a match becomes the naive
// midnight DRF makes aware in UTC; anything else is validated as sent.
func addDate(value any) (any, error) {
	text, ok := value.(string)
	if !ok {
		return value, nil
	}
	moment, matched, err := paymentWritesStrptime(text)
	if err != nil {
		return nil, err
	}
	if matched {
		return dto.PaymentEntryDate{Moment: moment}, nil
	}
	return value, nil
}

func replaceField(object domain.RequestObject, name string, value any) domain.RequestObject {
	out := make(domain.RequestObject, 0, len(object)+1)
	replaced := false
	for _, field := range object {
		if field.Name == name {
			field.Value, replaced = value, true
		}
		out = append(out, field)
	}
	if !replaced {
		out = append(out, domain.RequestField{Name: name, Value: value})
	}
	return out
}

func dropField(object domain.RequestObject, name string) domain.RequestObject {
	out := make(domain.RequestObject, 0, len(object))
	for _, field := range object {
		if field.Name != name {
			out = append(out, field)
		}
	}
	return out
}

// related runs the related fields' queryset lookups and the tenant scope:
// an object of another association raises the generic PermissionDenied,
// escaping every collected error.
func (s *PaymentWrites) related(ctx context.Context, tx domain.PaymentWritesTransaction, identity domain.ResolvedIdentity,
	input dto.PaymentEntryInput, invalid *dto.PaymentEntryValidationError) (*dto.PaymentEntryValidationError, error) {
	tenant, err := tx.TenantAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	var foreign bool
	check := func(name string, related dto.InvoicePartyRelated) error {
		if related.ID == nil {
			return nil
		}
		var found bool
		var owner *string
		var err error
		if name == "sport_association" {
			found, err = tx.AssociationExists(ctx, *related.ID)
			owner = related.ID
		} else {
			found, owner, err = tx.RelatedOwner(ctx, name, *related.ID)
		}
		if err != nil {
			return err
		}
		if !found {
			if invalid == nil {
				invalid = &dto.PaymentEntryValidationError{}
			}
			invalid.Insert(name, "related-does-not-exist:"+related.Raw)
			return nil
		}
		if tenant != nil && owner != nil && *owner != *tenant {
			foreign = true
		}
		return nil
	}
	for _, field := range []struct {
		name    string
		related dto.InvoicePartyRelated
	}{{"payment_category", input.Category}, {"sport_association", input.Association}, {"custom_accounts", input.Account}} {
		if err := check(field.name, field.related); err != nil {
			return nil, err
		}
	}
	if foreign {
		return nil, domain.ErrPaymentWritesForeignReference
	}
	return invalid, nil
}

// metaAmount is PaymentEntrySerializer.save()'s hook: with a non-None
// meta_payment_categories the amount becomes float(initial amount) plus the
// float of every meta amount (a Python exception is a 500).
func metaAmount(object domain.RequestObject) (*float64, error) {
	meta, present := object.Lookup("meta_payment_categories")
	if !present || meta == nil {
		return nil, nil
	}
	initial, _ := object.Lookup("amount")
	base, ok := dto.PythonFloatOf(initial)
	if !ok || initial == nil {
		return nil, domain.ErrPaymentWritesServer
	}
	var items []any
	switch v := meta.(type) {
	case []any:
		items = v
	case string:
		if v != "" {
			return nil, domain.ErrPaymentWritesServer
		}
	case domain.RequestObject:
		if len(v) > 0 {
			return nil, domain.ErrPaymentWritesServer
		}
	default:
		return nil, domain.ErrPaymentWritesServer
	}
	sum := 0.0
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrPaymentWritesServer
		}
		value, _ := object.Lookup("amount")
		number, ok := dto.PythonFloatOf(value)
		if !ok || value == nil {
			return nil, domain.ErrPaymentWritesServer
		}
		sum += number
	}
	total := base + sum
	if math.IsInf(total, 0) || math.IsNaN(total) {
		// DecimalField.to_python rejects the non-finite value before the write.
		return nil, domain.ErrPaymentWritesServer
	}
	return &total, nil
}

// paymentAttachment is one attachments_data item _handle_documents stores.
type paymentAttachment struct {
	content  []byte
	filename string
	// invalid is b64decode's binascii.Error / ValueError (the 500 page).
	invalid bool
	// unlinked is a Document that is created (filename str() of the value) and then
	// fails: os.path.join or default_storage.save raises before attachments.add.
	unlinked bool
}

// paymentAttachmentPlan is _handle_documents' loop: the items stored in
// order, then failing when an item lacks document or filename (its
// ValidationError after the earlier ones are stored) or invalid base64 stops
// the loop (500).
type paymentAttachmentPlan struct {
	stored  []paymentAttachment
	failing bool
}

// attachmentsStored plans attachments_data before any write. A document that is
// not base64 text, or a filename the INSERT rejects (null, NUL, over 255
// characters), is a 500 after the items before it. Items outside the ported
// subset are refused: a filename that is not text (str() stored, then
// os.path.join raises), a storage name Storage.save rejects (the Document
// stays), or no storage configured.
func (s *PaymentWrites) attachmentsStored(input dto.PaymentEntryInput) (paymentAttachmentPlan, error) {
	plan := paymentAttachmentPlan{}
	for _, item := range input.Attachments {
		document, hasDocument := item.Lookup("document")
		filename, hasFilename := item.Lookup("filename")
		if !hasDocument || !hasFilename {
			plan.failing = true
			return plan, nil
		}
		if s.documents == nil {
			return plan, domain.ErrPaymentWritesNotPorted
		}
		// base64.b64decode comes first: a document that is not a string (TypeError) or not base64 fails before any Document exists.
		text, isText := document.(string)
		if !isText {
			plan.stored = append(plan.stored, paymentAttachment{invalid: true})
			return plan, nil
		}
		content, err := dto.PythonB64Decode(text)
		if err != nil {
			plan.stored = append(plan.stored, paymentAttachment{invalid: true})
			return plan, nil
		}
		// Document.objects.create: a null name, a NUL or more than 255 characters fail the INSERT (no row).
		if filename == nil {
			plan.stored = append(plan.stored, paymentAttachment{invalid: true})
			return plan, nil
		}
		name, ok := filename.(string)
		if ok && (strings.ContainsRune(name, 0) || utf8.RuneCountInString(name) > 255) {
			plan.stored = append(plan.stored, paymentAttachment{invalid: true})
			return plan, nil
		}
		// A name that is not text is stored as str(value) and os.path.join then raises; a name
		// default_storage.save rejects leaves the created Document behind. Either way: the 500 page.
		if !ok {
			text := dto.PythonStr(filename)
			if utf8.RuneCountInString(text) > 255 {
				plan.stored = append(plan.stored, paymentAttachment{invalid: true})
			} else {
				plan.stored = append(plan.stored, paymentAttachment{filename: text, unlinked: true})
			}
			return plan, nil
		}
		if !utf8.ValidString(name) {
			plan.stored = append(plan.stored, paymentAttachment{invalid: true}) // the INSERT cannot encode it
			return plan, nil
		}
		if _, err := documentkey.UploadName(time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC), uuid.New(), name); err != nil {
			// Storage.save raises SuspiciousFileOperation (a 400 page, the Document stays): not ported.
			return plan, domain.ErrPaymentWritesNotPorted
		}
		plan.stored = append(plan.stored, paymentAttachment{content: content, filename: name})
	}
	return plan, nil
}

// storeAttachments runs the plan: per item Document.objects.create (audited
// create indexed to the actor's own association), the default_storage.save
// of the decoded bytes under <timestamp>/<id>/<filename> and
// obj.attachments.add. It returns the error Django raises after them.
func (s *PaymentWrites) storeAttachments(ctx context.Context, tx domain.PaymentWritesTransaction, identity domain.ResolvedIdentity,
	audit domain.LoginAuditContext, payment string, plan paymentAttachmentPlan) error {
	if len(plan.stored) > 0 {
		actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
		if err != nil {
			return err
		}
		auditContext := paymentCategoryAuditContext(identity, audit)
		for _, item := range plan.stored {
			if item.invalid {
				return domain.ErrPaymentWritesServer
			}
			at := s.at()
			document := domain.NewDocument{ID: s.newID(), Token: s.newID(), Filename: item.filename, Created: at}
			if err := tx.AttachPaymentDocument(ctx, payment, document, !item.unlinked, actorAssociation, auditContext, at); err != nil {
				return err
			}
			if item.unlinked {
				return domain.ErrPaymentWritesServer
			}
			if err := s.documents.save(ctx, document, item.content); err != nil {
				return err
			}
		}
	}
	if plan.failing {
		return domain.ErrPaymentWritesAttachment
	}
	return nil
}

// paymentMemory is the in-memory payment: the row to write plus the
// renderings that differ from the stored values (auditlog's str() of a
// float or validated amount, json.dumps of request JSON).
type paymentMemory struct {
	row         domain.PaymentWriteRow
	amountAudit *string
	amountShown *string
	// amountFloat is the in-memory amount when it is a Python float.
	amountFloat *float64
	jsonAudit   map[string]string
	// groupSent is the X-Group-Id text the in-memory payment keeps when the
	// column stores another spelling of the same uuid.
	groupSent *string
}

func jsonText(value any) *string {
	if value == nil {
		return nil
	}
	text := string(dto.PythonJSONDumps(value))
	return &text
}

func (m *paymentMemory) setJSON(field string, value any, target **string) {
	*target = jsonText(value)
	m.jsonAudit[field] = string(dto.PythonJSONDumpsSorted(value))
}

func (m *paymentMemory) setFloat(value float64) {
	m.row.Amount = dto.DecimalFromFloat9(value)
	audit, shown := dto.PythonFloatStr(value), dto.PaymentFloatHalfEven(value)
	m.amountAudit, m.amountShown, m.amountFloat = &audit, &shown, &value
}

func (m *paymentMemory) setDecimal(text string) {
	m.row.Amount = text
	m.amountAudit, m.amountShown, m.amountFloat = &text, &text, nil
}

// apply sets the validated fields (serializer.update's setattr / create's kwargs).
func (m *paymentMemory) apply(input dto.PaymentEntryInput, total *float64) {
	if input.Type != nil {
		m.row.Type = *input.Type
	}
	if input.Subject != nil {
		m.row.Subject = *input.Subject
	}
	if total != nil {
		m.setFloat(*total)
	} else if input.Amount != nil {
		m.setDecimal(*input.Amount)
	}
	if input.Description.Present {
		m.row.Description = input.Description.Value
	}
	if input.Notes.Present {
		m.row.Notes = input.Notes.Value
	}
	if input.Expense != nil {
		m.row.Expense = *input.Expense
	}
	if input.Paid != nil {
		m.row.Paid = *input.Paid
	}
	if input.PaymentDatePresent {
		m.row.PaymentDate = input.PaymentDate
	}
	if input.CreationDate != nil {
		m.row.CreationDate = *input.CreationDate
	}
	if input.Category.Present {
		m.row.CategoryID = input.Category.ID
	}
	if input.Association.Present {
		m.row.AssociationID = input.Association.ID
	}
	if input.Account.Present {
		m.row.AccountID = input.Account.ID
	}
	if input.Course.Present {
		m.setJSON("course", input.Course.Value, &m.row.Course)
	}
	if input.Meta.Present {
		m.setJSON("meta_payment_categories", input.Meta.Value, &m.row.MetaCategories)
	}
}

// defaults is Payment.save()'s category and description hooks.
func (s *PaymentWrites) defaults(ctx context.Context, tx domain.PaymentWritesTransaction, m *paymentMemory) error {
	save := domain.PaymentSaveRow{ID: m.row.ID, Subject: m.row.Subject, CategoryID: m.row.CategoryID, Description: m.row.Description,
		Meta: m.row.Meta, Course: m.row.Course}
	if save.CategoryID != nil {
		name, err := tx.CategoryName(ctx, *save.CategoryID)
		if err != nil {
			return err
		}
		save.CategoryName = name
	}
	if err := secCategorySaveDefaults(ctx, tx, &save, m.row.AssociationID, map[string][2]string{}); err != nil {
		if errors.Is(err, ErrPaymentSaveFailed) {
			return domain.ErrPaymentWritesServer
		}
		return err
	}
	m.row.CategoryID, m.row.Description = save.CategoryID, save.Description
	return nil
}

// values is the stored rendering with the in-memory overrides.
func (s *PaymentWrites) values(ctx context.Context, tx domain.PaymentWritesTransaction, m *paymentMemory) (map[string]string, error) {
	values, err := tx.PaymentAuditValues(ctx, m.row.ID)
	if err != nil {
		return nil, err
	}
	if m.amountAudit != nil {
		values["amount"] = *m.amountAudit
	}
	for field, text := range m.jsonAudit {
		values[field] = text
	}
	if m.groupSent != nil {
		values["group"] = *m.groupSent
	}
	return values, nil
}

var paymentAuditNullable = map[string]bool{"creation_date": true, "payment_date": true, "payment_category": true, "invoice": true, "user": true,
	"associate": true, "supplier": true, "instructor": true, "sport_association": true, "custom_accounts": true, "signature": true,
	"group": true}

func createdPaymentChanges(values map[string]string) map[string][2]string {
	changes := map[string][2]string{}
	for field, value := range values {
		if value == "None" && !paymentAuditNullable[field] {
			continue
		}
		changes[field] = [2]string{"None", value}
	}
	return changes
}

func updatedPaymentChanges(old, new map[string]string) map[string][2]string {
	changes := map[string][2]string{}
	for field, value := range new {
		if old[field] != value {
			changes[field] = [2]string{old[field], value}
		}
	}
	return changes
}

// audit appends one Payment entry; the index resolver reads the stored row
// (before an update: auditlog writes it in pre_save), then the changes'
// sport_association, then the actor's own association.
func (s *PaymentWrites) audit(ctx context.Context, tx domain.PaymentWritesTransaction, identity domain.ResolvedIdentity, audit domain.LoginAuditContext,
	id string, action int, changes map[string][2]string, stored *string) error {
	entry := domain.PaymentWritesAuditEntry{PaymentID: id, Action: action, Changes: changes, Context: paymentCategoryAuditContext(identity, audit),
		At: s.at()}
	if stored != nil {
		entry.Association, entry.Path = stored, "direct"
	} else {
		if pair, ok := changes["sport_association"]; ok {
			for _, candidate := range pair {
				if candidate == "" || strings.ToLower(candidate) == "none" {
					continue
				}
				live, err := tx.LiveAssociation(ctx, candidate)
				if err != nil {
					return err
				}
				if live {
					value := candidate
					entry.Association, entry.Path = &value, "changes_json.sport_association"
					break
				}
			}
		}
		if entry.Association == nil {
			actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
			if err != nil {
				return err
			}
			entry.Association, entry.Path = actor, "actor.sport_association"
		}
	}
	return tx.AppendPaymentWritesAudit(ctx, entry)
}

type paymentParty struct {
	associate, instructor, supplier *string
	setAssociate                    bool
}

// errPaymentWritesForeignParty is the views' PermissionDenied for a party of another association.
var errPaymentWritesForeignParty = &domain.PermissionDenied{Detail: "User not allowed."}

// paymentWritesOwned is a party's owning key compared with the caller's.
func paymentWritesOwned(found bool, owning *string, err error, caller string) error {
	if err != nil {
		return err
	}
	if !found {
		return domain.ErrPaymentWritesServer
	}
	if owning == nil || *owning != caller {
		return errPaymentWritesForeignParty
	}
	return nil
}

// party is the views' complex_item branch (a Python exception is a 500); payment parties only apply to
// the caller's association (instructors through the association's user).
func (s *PaymentWrites) party(ctx context.Context, tx domain.PaymentWritesTransaction, item any, group, scope *string,
	association, owner string) (paymentParty, error) {
	object, ok := item.(domain.RequestObject)
	if !ok {
		return paymentParty{}, domain.ErrPaymentWritesServer
	}
	kind, present := object.Lookup("group")
	if !present {
		return paymentParty{}, domain.ErrPaymentWritesServer
	}
	name, _ := kind.(string)
	lookup := func(key string) (string, error) {
		value, present := object.Lookup(key)
		if !present {
			return "", domain.ErrPaymentWritesServer
		}
		id, ok := dto.DjangoUUIDLookup(value)
		if !ok {
			return "", domain.ErrPaymentWritesServer
		}
		return id, nil
	}
	party := paymentParty{}
	switch name {
	case "Istruttori":
		id, err := lookup("instructor_id")
		if err != nil {
			return party, err
		}
		found, user, err := tx.InstructorOwner(ctx, id)
		if err = paymentWritesOwned(found, user, err, owner); err != nil {
			return party, err
		}
		party.instructor = &id
	case "Fornitori":
		id, err := lookup("supplier_id")
		if err != nil {
			return party, err
		}
		found, owning, err := tx.SupplierAssociation(ctx, id, group)
		if err = paymentWritesOwned(found, owning, err, association); err != nil {
			return party, err
		}
		party.supplier = &id
	case "Iscrizioni":
		id, err := lookup("subscription_id")
		if err != nil {
			return party, err
		}
		found, associate, owning, err := tx.SubscriptionAssociate(ctx, id, group, scope)
		if err = paymentWritesOwned(found, owning, err, association); err != nil {
			return party, err
		}
		party.associate, party.setAssociate = associate, true
	case "Persone":
		id, err := lookup("associate_id")
		if err != nil {
			return party, err
		}
		found, owning, err := tx.AssociateAssociation(ctx, id, group, scope)
		if err = paymentWritesOwned(found, owning, err, association); err != nil {
			return party, err
		}
		party.associate, party.setAssociate = &id, true
	}
	return party, nil
}

func (s *PaymentWrites) render(ctx context.Context, tx domain.PaymentWritesTransaction, m *paymentMemory, group *string) (map[string]any, error) {
	render, err := tx.RenderPayment(ctx, m.row.ID, group)
	if err != nil {
		return nil, err
	}
	item, err := dto.PaymentStateItem(render)
	if err != nil {
		return nil, err
	}
	if m.amountShown != nil {
		item["amount"] = *m.amountShown
	}
	if m.groupSent != nil {
		item["group"] = *m.groupSent
	}
	return item, nil
}

// entry validates the serializer input: the related lookups and tenant scope, then the field errors.
func (s *PaymentWrites) entry(ctx context.Context, tx domain.PaymentWritesTransaction, identity domain.ResolvedIdentity, object domain.RequestObject,
	partial bool) (dto.PaymentEntryInput, error) {
	for _, name := range []string{"payment_id"} {
		// A primary key in the body is not ported.
		if _, present := object.Lookup(name); present {
			return dto.PaymentEntryInput{}, domain.ErrPaymentWritesNotPorted
		}
	}
	input, invalid := dto.ParsePaymentEntry(object, partial)
	invalid, err := s.related(ctx, tx, identity, input, invalid)
	if err != nil {
		return input, err
	}
	if invalid != nil {
		return input, invalid
	}
	return input, nil
}

// createGroup is GroupAwareManager.create's raw X-Group-Id: a value the
// UUIDField rejects, or an unknown group (deferred foreign key), fails the
// create; another spelling of the uuid is refused here (createGroupSent
// serves it).
func (s *PaymentWrites) createGroup(ctx context.Context, tx domain.PaymentWritesTransaction, header string) (*string, error) {
	group, sent, err := s.createGroupSent(ctx, tx, header)
	if err == nil && sent != nil {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	return group, err
}

// createGroupSent is createGroup for every spelling the UUIDField accepts:
// the column stores the uuid; sent is the header text when it differs (the
// in-memory payment keeps it).
func (s *PaymentWrites) createGroupSent(ctx context.Context, tx domain.PaymentWritesTransaction, header string) (group, sent *string, err error) {
	if header == "" {
		return nil, nil, nil
	}
	group = supplierGroup(header)
	if group == nil {
		return nil, nil, domain.ErrPaymentWritesServer
	}
	exists, err := tx.GroupExists(ctx, *group)
	if err != nil {
		return nil, nil, err
	}
	if !exists {
		return nil, nil, domain.ErrPaymentWritesServer
	}
	if *group != header {
		sent = &header
	}
	return group, sent, nil
}

// paymentWritesFees is payment_add's fee arithmetic on the in-memory payment:
// invoiceFees for a validated Decimal amount; a float amount (the serializer's
// meta_payment_categories sum, or the model default) stays a float fee.
func paymentWritesFees(m *paymentMemory) (invoiceFeePair, error) {
	payment := domain.InvoiceGeneratePayment{Amount: m.row.Amount, MetaPaymentCategories: m.row.MetaCategories}
	if m.amountFloat == nil {
		return invoiceFeesAny(m.row.Subject, payment)
	}
	amount := *m.amountFloat
	if m.row.Subject != 1 {
		zero, _ := floatFee(0)
		activity, err := floatFeeAny(amount)
		return invoiceFeePair{membership: zero, activity: activity}, err
	}
	// The activity fee is the float sum of the meta amounts.
	payment.Amount = "0"
	fees, err := invoiceFeesAny(1, payment)
	if err != nil {
		return fees, err
	}
	if amount > 0 {
		activity, err := strconv.ParseFloat(fees.activity.memory, 64)
		if err != nil {
			return fees, domain.ErrPaymentStateServer
		}
		amount -= activity
	}
	fees.membership, err = floatFeeAny(amount)
	return fees, err
}

// addInvoice is payment_add's atomic block for a paid income payment with a
// party: the fees, the next invoice number by creation date (advisory lock)
// and the description; without a supplier the invoice (saved again with the
// payment date when payment_date_equal_invoice_date) and its eager print,
// which runs before the payment's last save. It returns the created
// invoice's fees (nil with a supplier).
func (s *PaymentWrites) addInvoice(ctx context.Context, tx domain.PaymentWritesTransaction, identity domain.ResolvedIdentity,
	request PaymentWritesRequest, m *paymentMemory, association string, group *string) (*invoiceFeePair, error) {
	invoices := tx.InvoiceGenerate()
	row, err := invoices.PaymentState(ctx, domain.PaymentStateQuery{ID: m.row.ID, Scope: paymentStateScope(identity), Group: group})
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, errors.New("payment add: created payment not found")
	}
	payment, err := invoices.InvoiceGeneratePayment(ctx, m.row.ID)
	if err != nil {
		return nil, err
	}
	fees, err := paymentWritesFees(m)
	if err != nil {
		return nil, err
	}
	owner, err := invoices.InvoiceGenerateOwner(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	number, err := (&InvoiceGenerations{now: s.now}).nextNumber(ctx, invoices, owner, association, group, m.row.CreationDate)
	if err != nil {
		return nil, err
	}
	description, err := (&InvoiceGenerations{}).description(ctx, invoices, row, payment, row.AssociateID, association, group)
	if err != nil {
		return nil, err
	}
	if m.row.SupplierID != nil {
		return nil, nil
	}
	invoiceGroup, sentGroup, err := paymentPaidFlowSentGroup(request.GroupHeader)
	if err != nil {
		return nil, err
	}
	if sentGroup != nil && *sentGroup == *invoiceGroup {
		sentGroup = nil
	}
	at := s.at()
	audit := paymentCategoryAuditContext(identity, request.Audit)
	create := domain.InvoiceGenerateCreate{ID: s.newID(), Association: association, CreationDate: at, MembershipFee: fees.membership.stored,
		ActivityFee: fees.activity.stored, MetaPaymentCategories: payment.MetaPaymentCategories, Description: &description, Number: number,
		Group: invoiceGroup}
	overrides := map[string]string{"membership_fee": fees.membership.memory, "activity_fee": fees.activity.memory}
	if sentGroup != nil {
		overrides["group"] = *sentGroup
	}
	if err = invoices.CreateInvoice(ctx, create, overrides, audit, at); err != nil {
		return nil, err
	}
	if owner.PaymentDateEqual {
		if m.row.PaymentDate == nil {
			return nil, errors.New("payment add: paid payment without a payment date")
		}
		// invoice.save(): the payment date and the float fees against the stored row.
		changes := map[string][2]string{}
		if before, after := courseSubscriptionAuditTime(&at), courseSubscriptionAuditTime(m.row.PaymentDate); before != after {
			changes["creation_date"] = [2]string{before, after}
		}
		if sentGroup != nil {
			changes["group"] = [2]string{*invoiceGroup, *sentGroup}
		}
		for field, fee := range map[string]invoiceFee{"membership_fee": fees.membership, "activity_fee": fees.activity} {
			if fee.column != fee.memory {
				changes[field] = [2]string{fee.column, fee.memory}
			}
		}
		if err = invoices.SetInvoiceCreationDate(ctx, create.ID, association, *m.row.PaymentDate, changes, audit, at); err != nil {
			return nil, err
		}
	}
	m.row.InvoiceID = &create.ID
	// print_document_invoice.apply(args=[invoice_id, Authorization]) inside the atomic block.
	if err = s.printer.Invoice(ctx, create.ID, request.Authorization); err != nil {
		return nil, err
	}
	return &fees, nil
}

// Add follows payment_add. serializer.save() creates the payment before the
// view sets its user, association and party, so a failure after it keeps the
// created payment. With auto_paid_payment the payment is paid (now, without
// a payment date) and an income payment with a party runs addInvoice, then
// is saved once more.
func (s *PaymentWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, request PaymentWritesRequest, body any) (map[string]any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrPaymentWritesAthlete
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	object, ok := body.(domain.RequestObject)
	if !ok || request.Form {
		// data['paid'] = ...: a list is not indexable and a QueryDict is immutable.
		return nil, domain.ErrPaymentWritesServer
	}
	owner := identity.ScopeUser.ID
	autoPaid, err := tx.AutoPaid(ctx, owner)
	if err != nil {
		return nil, err
	}
	if autoPaid && s.printer == nil {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	object = replaceField(object, "paid", autoPaid)
	creation, present := object.Lookup("creation_date")
	if !present {
		return nil, domain.ErrPaymentWritesServer
	}
	if creation == nil || creation == "" {
		creation = dto.PaymentEntryDate{Moment: s.at()}
	} else if creation, err = addDate(creation); err != nil {
		return nil, err
	}
	object = replaceField(object, "creation_date", creation)
	paymentDate, present := object.Lookup("payment_date")
	if !present {
		return nil, domain.ErrPaymentWritesServer
	}
	if paymentDate == "" {
		paymentDate = nil
	}
	if paymentDate == nil && autoPaid {
		// timezone.now() as text: strptime('%Y-%m-%d') does not match it and the serializer parses it.
		paymentDate = dto.PaymentEntryDate{Moment: s.at()}
	} else if paymentDate != nil {
		if paymentDate, err = addDate(paymentDate); err != nil {
			return nil, err
		}
	}
	object = replaceField(object, "payment_date", paymentDate)
	input, err := s.entry(ctx, tx, identity, object, false)
	if err != nil {
		return nil, err
	}
	if input.UserPresent {
		// serializer.save(): Payment.objects.create(user=<dict>) raises.
		return nil, domain.ErrPaymentWritesServer
	}
	total, err := metaAmount(object)
	if err != nil {
		return nil, err
	}
	attachments, err := s.attachmentsStored(input)
	if err != nil {
		return nil, err
	}
	group, sentGroup, err := s.createGroupSent(ctx, tx, request.GroupHeader)
	if err != nil {
		return nil, err
	}
	m := &paymentMemory{row: domain.PaymentWriteRow{ID: s.newID(), Type: "default", GroupID: group}, jsonAudit: map[string]string{},
		groupSent: sentGroup}
	// The model default amount is the float 0.00.
	m.setFloat(0)
	m.apply(input, total)
	if item, present := object.Lookup("complex_item"); present && dto.PythonTruthy(item) && m.row.Paid && !m.row.Expense {
		// A float fee the invoice columns round differently is refused before any write.
		if _, err := paymentWritesFees(m); errors.Is(err, domain.ErrPaymentStateNotPorted) {
			return nil, domain.ErrPaymentWritesNotPorted
		}
	}
	if err = s.defaults(ctx, tx, m); err != nil {
		return nil, err
	}
	if err = tx.InsertPaymentRow(ctx, m.row); err != nil {
		return nil, err
	}
	created, err := s.values(ctx, tx, m)
	if err != nil {
		return nil, err
	}
	if err = s.audit(ctx, tx, identity, request.Audit, m.row.ID, 0, createdPaymentChanges(created), m.row.AssociationID); err != nil {
		return nil, err
	}
	// From here on Django has committed the created payment.
	fail := func(failure error) (map[string]any, error) {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, failure
	}
	if err := s.storeAttachments(ctx, tx, identity, request.Audit, m.row.ID, attachments); errors.Is(err, domain.ErrPaymentWritesAttachment) ||
		errors.Is(err, domain.ErrPaymentWritesServer) {
		return fail(err)
	} else if err != nil {
		return nil, err
	}
	item, present := object.Lookup("complex_item")
	if !present {
		return fail(domain.ErrPaymentWritesServer)
	}
	scope := paymentStateScope(identity)
	readGroup := supplierGroup(request.GroupHeader)
	stored := m.row.AssociationID
	before, err := tx.PaymentAuditValues(ctx, m.row.ID)
	if err != nil {
		return nil, err
	}
	if dto.PythonTruthy(item) {
		party, err := s.party(ctx, tx, item, readGroup, scope, association, owner)
		if errors.Is(err, domain.ErrPaymentWritesServer) || errors.Is(err, errPaymentWritesForeignParty) {
			return fail(err)
		}
		if err != nil {
			return nil, err
		}
		if party.instructor != nil {
			m.row.InstructorID = party.instructor
		}
		if party.supplier != nil {
			m.row.SupplierID = party.supplier
		}
		if party.setAssociate {
			m.row.AssociateID = party.associate
		}
	}
	m.row.UserID, m.row.AssociationID = &owner, &association
	if err = s.defaults(ctx, tx, m); err != nil {
		return fail(err)
	}
	if err = tx.SavePaymentRow(ctx, m.row); err != nil {
		return nil, err
	}
	after, err := s.values(ctx, tx, m)
	if err != nil {
		return nil, err
	}
	if changes := updatedPaymentChanges(before, after); len(changes) > 0 {
		if err = s.audit(ctx, tx, identity, request.Audit, m.row.ID, 1, changes, stored); err != nil {
			return nil, err
		}
	}
	var fees *invoiceFeePair
	if m.row.Paid && m.row.InvoiceID == nil && !m.row.Expense &&
		(m.row.AssociateID != nil || m.row.SupplierID != nil || m.row.InstructorID != nil) {
		fees, err = s.addInvoice(ctx, tx, identity, request, m, association, readGroup)
		if errors.Is(err, domain.ErrPaymentStateServer) {
			// Raised before the invoice exists: the atomic block has nothing to undo and the saved payment stays.
			return fail(domain.ErrPaymentWritesServer)
		}
		if err != nil {
			return nil, err
		}
		// payment.save() after the block.
		if before, err = tx.PaymentAuditValues(ctx, m.row.ID); err != nil {
			return nil, err
		}
		if err = s.defaults(ctx, tx, m); err != nil {
			return nil, err
		}
		if err = tx.SavePaymentRow(ctx, m.row); err != nil {
			return nil, err
		}
		if after, err = s.values(ctx, tx, m); err != nil {
			return nil, err
		}
		if changes := updatedPaymentChanges(before, after); len(changes) > 0 {
			if err = s.audit(ctx, tx, identity, request.Audit, m.row.ID, 1, changes, &association); err != nil {
				return nil, err
			}
		}
	}
	result, err := s.render(ctx, tx, m, readGroup)
	if err != nil {
		return nil, err
	}
	if invoice, ok := result["invoice"].(map[string]any); ok && fees != nil {
		// The in-memory invoice keeps the float fees.
		invoice["membership_fee"], invoice["activity_fee"] = fees.membership.rendered, fees.activity.rendered
		if m.groupSent != nil {
			invoice["group"] = *m.groupSent
		}
	}
	return result, tx.Commit()
}

// Update follows payment_update: is_valid_uuid, the association, the
// payment (none or without association: 500; another association: 403),
// the partial serializer, the parties, the save hook and the audited
// full-row save, then attachments_data. Invoiced payments (PDF task) and
// form bodies are refused before any write.
func (s *PaymentWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, request PaymentWritesRequest, body any) (map[string]any, error) {
	id, err := messageReadsParseUUID(request.ID)
	if err != nil {
		return nil, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	group, scope := supplierGroup(request.GroupHeader), paymentStateScope(identity)
	row, err := tx.LockPayment(ctx, id, group, scope)
	if err != nil {
		return nil, err
	}
	if row == nil || row.AssociationID == nil {
		return nil, domain.ErrPaymentWritesServer
	}
	if *row.AssociationID != association {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrPaymentWritesServer
	}
	if request.Form {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	for _, name := range []string{"creation_date", "payment_date"} {
		if value, present := object.Lookup(name); present && value == "" {
			object = dropField(object, name)
		}
	}
	// The row is the one named by the path: a payment_id in the body is dropped.
	object = dropField(object, "payment_id")
	input, err := s.entry(ctx, tx, identity, object, true)
	if err != nil {
		return nil, err
	}
	m := &paymentMemory{row: *row, jsonAudit: map[string]string{}}
	m.row.InstructorID, m.row.SupplierID = nil, nil
	if item, present := object.Lookup("complex_item"); present && dto.PythonTruthy(item) {
		m.row.AssociateID = nil
		party, err := s.party(ctx, tx, item, group, scope, association, identity.ScopeUser.ID)
		if err != nil {
			return nil, err
		}
		m.row.InstructorID, m.row.SupplierID = party.instructor, party.supplier
		if party.setAssociate {
			m.row.AssociateID = party.associate
		}
	}
	if value, present := object.Lookup("subscription_id"); present && dto.PythonTruthy(value) {
		subscription, ok := dto.DjangoUUIDLookup(value)
		if !ok {
			return nil, domain.ErrPaymentWritesServer
		}
		found, associate, owning, err := tx.SubscriptionAssociate(ctx, subscription, group, scope)
		if err = paymentWritesOwned(found, owning, err, association); err != nil {
			return nil, err
		}
		m.row.AssociateID = associate
	}
	m.row.PaymentIntentID = nil
	if input.UserPresent {
		// serializer.save(): update() refuses writable nested fields.
		return nil, domain.ErrPaymentWritesServer
	}
	total, err := metaAmount(object)
	if err != nil {
		return nil, err
	}
	if row.InvoiceID != nil && s.printer == nil {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	attachments, err := s.attachmentsStored(input)
	if err != nil {
		return nil, err
	}
	m.apply(input, total)
	if err = s.defaults(ctx, tx, m); err != nil {
		return nil, err
	}
	before, err := tx.PaymentAuditValues(ctx, id)
	if err != nil {
		return nil, err
	}
	if err = tx.SavePaymentRow(ctx, m.row); err != nil {
		return nil, err
	}
	after, err := s.values(ctx, tx, m)
	if err != nil {
		return nil, err
	}
	if changes := updatedPaymentChanges(before, after); len(changes) > 0 {
		if err = s.audit(ctx, tx, identity, request.Audit, id, 1, changes, row.AssociationID); err != nil {
			return nil, err
		}
	}
	if err := s.storeAttachments(ctx, tx, identity, request.Audit, id, attachments); errors.Is(err, domain.ErrPaymentWritesAttachment) ||
		errors.Is(err, domain.ErrPaymentWritesServer) {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, err
	} else if err != nil {
		return nil, err
	}
	item, err := s.render(ctx, tx, m, group)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	// print_document_invoice.apply(args=[invoice_id, Authorization]) runs
	// after the save; the response renders the payment as loaded before it.
	if m.row.InvoiceID != nil {
		if err = s.printer.Invoice(ctx, *m.row.InvoiceID, request.Authorization); err != nil {
			return nil, err
		}
	}
	return map[string]any{"data": map[string]any{"payment": item}}, nil
}

func paymentBulkMessage(text string) error { return &domain.PaymentWritesMessage{Message: text} }

// bulkSubject is `subject not in [0, 1, 2, 3]` (Python equality: booleans
// and integral floats match).
func bulkSubject(value any) (int, bool) {
	for subject := 0; subject <= 3; subject++ {
		if dto.PythonEqual(value, dto.JSONInteger(subject)) {
			return subject, true
		}
	}
	return 0, false
}

// bulkMember is the set() identity of one associate_ids member: ok false is
// the TypeError of an unhashable value.
func bulkMember(value any) (string, bool) {
	switch v := value.(type) {
	case nil:
		return "none", true
	case string:
		return "s:" + v, true
	case bool, json.Number:
		if number, ok := dto.PythonNumericKey(v); ok {
			return "n:" + number, true
		}
		return "f:" + dto.PythonStr(value), true
	}
	return "", false
}

// bulkDate is the bulk view's date handling of a non-blank value that is not
// a strptime match: DateTimeField.to_python at bulk_create (anything it
// rejects is a 500).
func bulkDate(value any) (*time.Time, error) {
	text, ok := value.(string)
	if !ok {
		return nil, domain.ErrPaymentWritesServer
	}
	moment, matched, err := paymentWritesStrptime(text)
	if err != nil {
		return nil, err
	}
	if matched {
		return &moment, nil
	}
	parsed, ok := dto.DjangoDateTimeToPython(text)
	if !ok {
		return nil, domain.ErrPaymentWritesServer
	}
	return &parsed, nil
}

// BulkAdd follows payment_bulk_add: the view's own checks (400 messages),
// the live group-aware associates of the association, the defaults it
// computes and one atomic bulk_create (no save(), signals or audit).
func (s *PaymentWrites) BulkAdd(ctx context.Context, identity domain.ResolvedIdentity, request PaymentWritesRequest, body any) (int, map[string]any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return 0, nil, domain.ErrPaymentWritesAthlete
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return 0, nil, err
	}
	defer tx.Rollback()
	object, ok := body.(domain.RequestObject)
	if !ok {
		return 0, nil, domain.ErrPaymentWritesServer
	}
	value, _ := object.Lookup("associate_ids")
	members, isList := value.([]any)
	if !dto.PythonTruthy(value) || !isList {
		return 0, nil, paymentBulkMessage("associate_ids is required and must be a non-empty list")
	}
	if len(members) > 10000 {
		return 0, nil, paymentBulkMessage("associate_ids cannot exceed 10,000 items")
	}
	amountValue, _ := object.Lookup("amount")
	if amountValue == nil {
		return 0, nil, paymentBulkMessage("amount is required")
	}
	amount := dto.ParsePythonDecimal(dto.PythonStr(amountValue))
	switch {
	case amount.Kind == "" || amount.Kind == "nan":
		return 0, nil, paymentBulkMessage("amount must be a valid decimal number")
	case amount.Negative && !amount.Zero:
		return 0, nil, paymentBulkMessage("amount must be >= 0")
	}
	paymentType := any("default")
	if value, present := object.Lookup("type"); present {
		paymentType = value
	}
	typeText, _ := paymentType.(string)
	valid := false
	for _, choice := range dto.PaymentTypes {
		valid = valid || typeText == choice
	}
	if _, isText := paymentType.(string); !valid || !isText {
		return 0, nil, paymentBulkMessage("type must be one of: ['default', 'cash', 'transfer', 'online', 'sepa-transfer', 'stripe', 'pos']")
	}
	subjectValue := any(dto.JSONInteger(0))
	if value, present := object.Lookup("subject"); present {
		subjectValue = value
	}
	subject, ok := bulkSubject(subjectValue)
	if !ok {
		return 0, nil, paymentBulkMessage("subject must be one of: [0, 1, 2, 3]")
	}
	// requested_ids = set(associate_ids): the first of equal members is kept.
	seen := map[string]bool{}
	var requested []any
	for _, member := range members {
		key, hashable := bulkMember(member)
		if !hashable {
			return 0, nil, domain.ErrPaymentWritesServer
		}
		if !seen[key] {
			seen[key] = true
			requested = append(requested, member)
		}
	}
	var lookups []string
	for _, member := range requested {
		if member == nil {
			continue
		}
		id, ok := dto.DjangoUUIDLookup(member)
		if !ok {
			return 0, nil, domain.ErrPaymentWritesServer
		}
		lookups = append(lookups, id)
	}
	scope, group := paymentStateScope(identity), supplierGroup(request.GroupHeader)
	found, err := tx.BulkAssociates(ctx, lookups, association, group, scope)
	if err != nil {
		return 0, nil, err
	}
	validIDs := map[string]bool{}
	for _, id := range found {
		validIDs[id] = true
	}
	invalid := []string{}
	for _, member := range requested {
		if text := dto.PythonStr(member); !validIDs[text] {
			invalid = append(invalid, text)
		}
	}
	answer := func(created int) map[string]any {
		return map[string]any{"data": map[string]any{"created_count": created, "total_requested": len(requested), "invalid_associate_ids": invalid}}
	}
	if len(found) == 0 {
		return 200, answer(0), tx.Commit()
	}
	var account *string
	if value, _ := object.Lookup("custom_accounts"); dto.PythonTruthy(value) {
		id, ok := dto.DjangoUUIDLookup(value)
		if !ok {
			return 0, nil, domain.ErrPaymentWritesServer
		}
		if account, err = tx.AssociationAccount(ctx, id, association); err != nil {
			return 0, nil, err
		}
	} else if account, err = tx.DefaultBankAccount(ctx, association); err != nil {
		return 0, nil, err
	}
	var category *string
	if value, _ := object.Lookup("payment_category"); dto.PythonTruthy(value) {
		id, ok := dto.DjangoUUIDLookup(value)
		if !ok {
			return 0, nil, domain.ErrPaymentWritesServer
		}
		exists, err := tx.AssociationCategoryExists(ctx, id, association)
		if err != nil {
			return 0, nil, err
		}
		if exists {
			category = &id
		}
	} else if category, _, err = tx.DefaultPaymentCategory(ctx, &association); err != nil {
		return 0, nil, err
	}
	var description *string
	if value, _ := object.Lookup("description"); value != nil {
		text := dto.PythonStr(value)
		description = &text
	} else if subject == 1 || subject == 2 {
		text := map[int]string{1: "Iscrizione", 2: "Quota Corso"}[subject]
		description = &text
	}
	paidValue, _ := object.Lookup("paid")
	if paidValue == nil {
		if paidValue, err = tx.AutoPaid(ctx, identity.ScopeUser.ID); err != nil {
			return 0, nil, err
		}
	}
	paid, ok := dto.DjangoBooleanToPython(paidValue)
	if !ok || paid == nil {
		return 0, nil, domain.ErrPaymentWritesServer
	}
	expenseValue := any(false)
	if value, present := object.Lookup("expense"); present {
		expenseValue = value
	}
	expense, ok := dto.DjangoBooleanToPython(expenseValue)
	if !ok || expense == nil {
		return 0, nil, domain.ErrPaymentWritesServer
	}
	now := s.at()
	creation := now
	if value, _ := object.Lookup("creation_date"); value != nil && value != "" {
		if text, ok := value.(string); ok {
			moment, matched, err := paymentWritesStrptime(text)
			if err != nil {
				return 0, nil, err
			}
			if matched {
				creation = moment
			}
		}
	}
	var paymentDate *time.Time
	dateValue, _ := object.Lookup("payment_date")
	if dateValue == "" {
		dateValue = nil
	}
	if dateValue == nil && paidValue == true {
		paymentDate = &now
	} else if dateValue != nil {
		if paymentDate, err = bulkDate(dateValue); err != nil {
			return 0, nil, err
		}
	}
	if amount.Kind != "finite" {
		// DecimalField.to_python rejects Infinity at bulk_create.
		return 0, nil, domain.ErrPaymentWritesServer
	}
	var notes *string
	if value, _ := object.Lookup("notes"); value != nil {
		text := dto.PythonStr(value)
		notes = &text
	}
	meta := jsonText([]any{})
	if value, present := object.Lookup("meta_payment_categories"); present {
		meta = jsonText(value)
	}
	owner := identity.ScopeUser.ID
	rows := make([]domain.PaymentBulkRow, 0, len(found))
	for _, associate := range found {
		rows = append(rows, domain.PaymentBulkRow{ID: s.newID(), AssociateID: associate, UserID: owner, AssociationID: association,
			Type: typeText, Subject: subject, Amount: amount.Text, Description: description, Notes: notes, CategoryID: category, AccountID: account,
			Paid: *paid, Expense: *expense, CreationDate: creation, PaymentDate: paymentDate, Meta: meta})
	}
	if err = tx.InsertBulkPayments(ctx, rows); err != nil {
		return 0, nil, err
	}
	return 201, answer(len(rows)), tx.Commit()
}
