package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// PaymentWrites ports payment_add, payment_update and payment_bulk_add.
type PaymentWrites struct {
	store   domain.PaymentWritesStore
	now     func() time.Time
	newID   func() string
	printer *InvoiceSyncPrint
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
// the ValueError the views catch. strptime's \d also matches non-ASCII
// decimal digits: not ported.
func paymentWritesStrptime(text string) (time.Time, bool, error) {
	for _, r := range text {
		if r > unicode.MaxASCII && unicode.IsDigit(r) {
			return time.Time{}, false, domain.ErrPaymentWritesNotPorted
		}
	}
	moment, err := paymentStateDate(text)
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

// attachmentsStored refuses attachments _handle_documents would store
// (the first item has document and filename); otherwise it reports whether
// the hook raises after the save.
func attachmentsStored(input dto.PaymentEntryInput) (bool, error) {
	if len(input.Attachments) == 0 {
		return false, nil
	}
	first := input.Attachments[0]
	_, document := first.Lookup("document")
	_, filename := first.Lookup("filename")
	if document && filename {
		return false, domain.ErrPaymentWritesNotPorted
	}
	return true, nil
}

// paymentMemory is the in-memory payment: the row to write plus the
// renderings that differ from the stored values (auditlog's str() of a
// float or validated amount, json.dumps of request JSON).
type paymentMemory struct {
	row         domain.PaymentWriteRow
	amountAudit *string
	amountShown *string
	jsonAudit   map[string]string
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
	m.amountAudit, m.amountShown = &audit, &shown
}

func (m *paymentMemory) setDecimal(text string) {
	m.row.Amount = text
	m.amountAudit, m.amountShown = &text, &text
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
	if m.row.AssociationID != nil {
		save.AssociationID = *m.row.AssociationID
	}
	if save.CategoryID != nil {
		name, err := tx.CategoryName(ctx, *save.CategoryID)
		if err != nil {
			return err
		}
		save.CategoryName = name
	}
	if err := paymentSaveDefaults(ctx, tx, &save, map[string][2]string{}); err != nil {
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

// party is the views' complex_item branch (a Python exception is a 500).
func (s *PaymentWrites) party(ctx context.Context, tx domain.PaymentWritesTransaction, item any, group, scope *string) (paymentParty, error) {
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
	missing := func(found bool, err error) error {
		if err != nil {
			return err
		}
		if !found {
			return domain.ErrPaymentWritesServer
		}
		return nil
	}
	party := paymentParty{}
	switch name {
	case "Istruttori":
		id, err := lookup("instructor_id")
		if err != nil {
			return party, err
		}
		if err = missing(tx.InstructorExists(ctx, id)); err != nil {
			return party, err
		}
		party.instructor = &id
	case "Fornitori":
		id, err := lookup("supplier_id")
		if err != nil {
			return party, err
		}
		if err = missing(tx.SupplierExists(ctx, id, group)); err != nil {
			return party, err
		}
		party.supplier = &id
	case "Iscrizioni":
		id, err := lookup("subscription_id")
		if err != nil {
			return party, err
		}
		found, associate, err := tx.SubscriptionAssociate(ctx, id, group, scope)
		if err = missing(found, err); err != nil {
			return party, err
		}
		party.associate, party.setAssociate = associate, true
	case "Persone":
		id, err := lookup("associate_id")
		if err != nil {
			return party, err
		}
		if err = missing(tx.AssociateExists(ctx, id, group, scope)); err != nil {
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
	return item, nil
}

// entry validates the serializer input: the related lookups and tenant scope, then the field errors.
func (s *PaymentWrites) entry(ctx context.Context, tx domain.PaymentWritesTransaction, identity domain.ResolvedIdentity, object domain.RequestObject,
	partial bool) (dto.PaymentEntryInput, error) {
	for _, name := range []string{"payment_id", "user"} {
		// A primary key change and the nested user write are not ported.
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
// create; a non-canonical spelling would be stored as sent (not ported).
func (s *PaymentWrites) createGroup(ctx context.Context, tx domain.PaymentWritesTransaction, header string) (*string, error) {
	if header == "" {
		return nil, nil
	}
	group := supplierGroup(header)
	if group == nil {
		return nil, domain.ErrPaymentWritesServer
	}
	if *group != header {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	exists, err := tx.GroupExists(ctx, *group)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, domain.ErrPaymentWritesServer
	}
	return group, nil
}

// Add follows payment_add for users without auto_paid_payment (the paid
// branch creates invoices and PDFs: not ported). serializer.save() creates
// the payment before the view sets its user, association and party, so a
// failure after it keeps the created payment.
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
	if autoPaid {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	object = replaceField(object, "paid", false)
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
	if paymentDate != nil {
		if paymentDate, err = addDate(paymentDate); err != nil {
			return nil, err
		}
	}
	object = replaceField(object, "payment_date", paymentDate)
	input, err := s.entry(ctx, tx, identity, object, false)
	if err != nil {
		return nil, err
	}
	total, err := metaAmount(object)
	if err != nil {
		return nil, err
	}
	failing, err := attachmentsStored(input)
	if err != nil {
		return nil, err
	}
	group, err := s.createGroup(ctx, tx, request.GroupHeader)
	if err != nil {
		return nil, err
	}
	m := &paymentMemory{row: domain.PaymentWriteRow{ID: s.newID(), Type: "default", GroupID: group}, jsonAudit: map[string]string{}}
	// The model default amount is the float 0.00.
	m.setFloat(0)
	m.apply(input, total)
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
	if failing {
		return fail(domain.ErrPaymentWritesAttachment)
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
		party, err := s.party(ctx, tx, item, readGroup, scope)
		if errors.Is(err, domain.ErrPaymentWritesServer) {
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
	result, err := s.render(ctx, tx, m, readGroup)
	if err != nil {
		return nil, err
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
	if value, present := object.Lookup("payment_id"); present {
		// The edit form posts the row's own key: UUIDField accepts it, the
		// UniqueValidator excludes the instance and the save keeps the row.
		// Any other key would move the primary key (not ported).
		if text, isText := value.(string); isText {
			if key, ok := dto.DjangoUUIDLookup(text); ok && key == row.ID {
				object = dropField(object, "payment_id")
			}
		}
	}
	input, err := s.entry(ctx, tx, identity, object, true)
	if err != nil {
		return nil, err
	}
	m := &paymentMemory{row: *row, jsonAudit: map[string]string{}}
	m.row.InstructorID, m.row.SupplierID = nil, nil
	if item, present := object.Lookup("complex_item"); present && dto.PythonTruthy(item) {
		m.row.AssociateID = nil
		party, err := s.party(ctx, tx, item, group, scope)
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
		found, associate, err := tx.SubscriptionAssociate(ctx, subscription, group, scope)
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, domain.ErrPaymentWritesServer
		}
		m.row.AssociateID = associate
	}
	m.row.PaymentIntentID = nil
	total, err := metaAmount(object)
	if err != nil {
		return nil, err
	}
	if row.InvoiceID != nil && s.printer == nil {
		return nil, domain.ErrPaymentWritesNotPorted
	}
	failing, err := attachmentsStored(input)
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
	if failing {
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		return nil, domain.ErrPaymentWritesAttachment
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
		exists, err := tx.CategoryExists(ctx, id)
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
