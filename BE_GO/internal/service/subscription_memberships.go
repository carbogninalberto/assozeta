package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

type SubscriptionMemberships struct {
	store   domain.SubscriptionMembershipStore
	now     func() time.Time
	ids     func() string
	uploads *DocumentUploads
}

// WithUploads enables documents_data (the document storage of documents-writes).
func (s *SubscriptionMemberships) WithUploads(uploads *DocumentUploads) *SubscriptionMemberships {
	s.uploads = uploads
	return s
}

func NewSubscriptionMemberships(store domain.SubscriptionMembershipStore, now func() time.Time, ids func() string) *SubscriptionMemberships {
	return &SubscriptionMemberships{store: store, now: now, ids: ids}
}

var (
	// ErrSubscriptionMembershipNoPK is get_object() on a route without a pk
	// (the viewset's delete() on the list and add routes): a 500.
	ErrSubscriptionMembershipNoPK = errors.New("subscription membership route has no pk")
	// ErrSubscriptionMembershipFilter is a query filter the UUID column
	// rejects (Django's ValidationError while building the queryset): a 500.
	ErrSubscriptionMembershipFilter = errors.New("subscription membership filter is not a UUID")
	// ErrSubscriptionMembershipBody is add's request.data[...] assignment on a
	// body that is not a mutable dict (lists, scalars, form QueryDicts): a 500.
	ErrSubscriptionMembershipBody = errors.New("subscription membership add body is not a mutable dict")
	// ErrSubscriptionMembershipDocumentKey is {'exception': 'document key not
	// present'} (400), raised after the membership was saved.
	ErrSubscriptionMembershipDocumentKey = errors.New("subscription membership document key not present")
	// ErrSubscriptionMembershipDocumentDecode is base64.b64decode's failure
	// (500), raised after the membership was saved.
	ErrSubscriptionMembershipDocumentDecode = errors.New("subscription membership document is not base64")
	// errSubscriptionMembershipDocumentStore is the exception Django raises
	// while creating or storing a document (the INSERT, os.path.join or
	// Storage.save): the 500 page, after the earlier writes.
	errSubscriptionMembershipDocumentStore = errors.New("subscription membership document could not be stored")
	// errSubscriptionMembershipNoStorage is a writer built without its
	// document storage: cmd/server/main.go always calls WithUploads. An
	// application-state error.
	errSubscriptionMembershipNoStorage = refusal.AppState("subscription memberships without document storage")
)

// SubscriptionMembershipValidationError is DRF's 400 field map.
type SubscriptionMembershipValidationError struct{ Fields map[string]any }

func (e *SubscriptionMembershipValidationError) Error() string {
	return "invalid subscription membership input"
}

// SubscriptionMembershipRequest carries what the view reads besides the body.
type SubscriptionMembershipRequest struct {
	Query       domain.RequestObject
	GroupHeader string
}

// queryset is get_queryset(): SportAssociation.objects.get(user=
// request.user), then the subscription and associate_id filters.
func subscriptionMembershipQueryset(ctx context.Context, tx domain.SubscriptionMembershipTransaction, identity domain.ResolvedIdentity,
	query domain.RequestObject) (string, domain.SubscriptionMembershipFilter, error) {
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return "", domain.SubscriptionMembershipFilter{}, err
	}
	filter := domain.SubscriptionMembershipFilter{}
	for _, item := range []struct {
		name   string
		target **string
	}{{"subscription", &filter.Subscription}, {"associate_id", &filter.Associate}} {
		value, _ := query.Lookup(item.name)
		text, _ := value.(string)
		if text == "" {
			continue
		}
		id, err := paymentInfoParseUUID(text)
		if err != nil {
			return "", filter, ErrSubscriptionMembershipFilter
		}
		canonical := id.String()
		*item.target = &canonical
	}
	return association, filter, nil
}

// membership is get_object(): the queryset, then the pk lookup (a pk the
// UUIDField rejects is the bare 404).
func (s *SubscriptionMemberships) membership(ctx context.Context, tx domain.SubscriptionMembershipTransaction, identity domain.ResolvedIdentity,
	raw string, query domain.RequestObject) (*domain.SubscriptionMembershipRow, string, error) {
	association, filter, err := subscriptionMembershipQueryset(ctx, tx, identity, query)
	if err != nil {
		return nil, "", err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, "", domain.ErrSubscriptionMembershipMalformed
	}
	key := id.String()
	rows, err := tx.Memberships(ctx, association, filter, &key)
	if err != nil {
		return nil, "", err
	}
	if len(rows) == 0 {
		return nil, "", domain.ErrSubscriptionMembershipNotFound
	}
	return &rows[0], association, nil
}

// subscriptionMembershipItem is SubscriptionMembershipSerializer(instance).data.
func subscriptionMembershipItem(row domain.SubscriptionMembership, paid *bool, documents string) (map[string]any, error) {
	decoded := []courseLocationDocument{}
	if err := json.Unmarshal([]byte(documents), &decoded); err != nil {
		return nil, err
	}
	rendered := make([]map[string]any, 0, len(decoded))
	for _, document := range decoded {
		rendered = append(rendered, map[string]any{"document_id": document.ID, "creation_date": dto.DRFUTCDateTime(document.CreationDate),
			"filepath": document.Filepath, "filename": document.Filename, "file_size_bytes": document.FileSizeBytes, "token": document.Token})
	}
	return map[string]any{"subscription_membership_id": row.ID, "sport_association": row.AssociationID, "associate_id": row.AssociateID,
		"associate": row.AssociateID, "creation_date": dto.DRFUTCDateTime(row.CreationDate), "start_date": row.StartDate,
		"end_date": row.EndDate, "membership_type": row.MembershipType, "membership_number": row.MembershipNumber,
		"description": row.Description, "price": row.Price, "paid": paid, "payment": row.PaymentID,
		"attached_membership_documents": rendered}, nil
}

// List follows SubscriptionMembershipViewSet.list ({'data': [...]}, unordered).
func (s *SubscriptionMemberships) List(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionMembershipRequest) (map[string]any, error) {
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, filter, err := subscriptionMembershipQueryset(ctx, tx, identity, request.Query)
	if err != nil {
		return nil, err
	}
	rows, err := tx.Memberships(ctx, association, filter, nil)
	if err != nil {
		return nil, err
	}
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		item, err := subscriptionMembershipItem(row.SubscriptionMembership, row.Paid, row.Documents)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return map[string]any{"data": items}, nil
}

// AddAssociation is add's User.sport_association property, evaluated before
// the body is read: athletes are refused, an owner without an association is
// a 500.
func (s *SubscriptionMemberships) AddAssociation(ctx context.Context, identity domain.ResolvedIdentity) error {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	return err
}

// validate applies the related querysets and tenant scope (its
// PermissionDenied escapes the serializer before the field errors).
func (s *SubscriptionMemberships) validate(ctx context.Context, tx domain.SubscriptionMembershipTransaction, association string,
	write dto.SubscriptionMembershipWrite, invalid map[string]any, group *string) error {
	if invalid == nil {
		invalid = map[string]any{}
	}
	foreign := false
	lookups := []struct {
		name     string
		relation dto.SubscriptionMembershipRelation
		find     func(string) (*domain.SubscriptionMembershipReference, error)
	}{
		{"sport_association", write.Association, func(id string) (*domain.SubscriptionMembershipReference, error) { return tx.Association(ctx, id) }},
		{"associate", write.Associate, func(id string) (*domain.SubscriptionMembershipReference, error) { return tx.Associate(ctx, id, group) }},
		{"payment", write.Payment, func(id string) (*domain.SubscriptionMembershipReference, error) { return tx.Payment(ctx, id, group) }},
	}
	for _, lookup := range lookups {
		if lookup.relation.ID == nil {
			continue
		}
		found, err := lookup.find(*lookup.relation.ID)
		if err != nil {
			return err
		}
		if found == nil {
			invalid[lookup.name] = []string{"related-does-not-exist:" + *lookup.relation.ID}
		} else if association != "" && found.AssociationID != nil && *found.AssociationID != association {
			// An empty association is no current tenant (tenant_scope does not enforce).
			foreign = true
		}
	}
	if foreign {
		return domain.ErrSubscriptionMembershipForeignReference
	}
	if len(invalid) > 0 {
		return &SubscriptionMembershipValidationError{Fields: invalid}
	}
	return nil
}

// subscriptionMembershipDocuments checks documents_data before any write: the
// items _handle_documents raises on after the save are left to it, and every
// item that would create a Document needs the document storage.
func (s *SubscriptionMemberships) subscriptionMembershipDocuments(write dto.SubscriptionMembershipWrite) error {
	for _, item := range write.Documents {
		switch dto.CourseLocationDocument(item) {
		case dto.CourseLocationDocumentMissingKey, dto.CourseLocationDocumentUndecodable:
			return nil // raised in order after the save
		}
		if s.uploads == nil {
			return errSubscriptionMembershipNoStorage
		}
	}
	return nil
}

// subscriptionMembershipDocumentName is str() of a filename: a str unchanged,
// an uploaded file its name, anything else its Python repr.
func subscriptionMembershipDocumentName(value any) string {
	switch name := value.(type) {
	case domain.RequestFile:
		return name.Name
	case domain.RequestUpload:
		return name.Name
	}
	return dto.PythonStr(value)
}

// handleDocuments is _handle_documents after the save, item by item (not
// atomic): Document.objects.create, default_storage.save, then the link; the
// first item without both keys raises the 400, an undecodable one the 500,
// and the work before it stays.
func (s *SubscriptionMemberships) handleDocuments(ctx context.Context, tx domain.SubscriptionMembershipTransaction, membership string,
	write dto.SubscriptionMembershipWrite, audit domain.LoginAuditContext) (int, error) {
	added := 0
	for _, item := range write.Documents {
		switch dto.CourseLocationDocument(item) {
		case dto.CourseLocationDocumentMissingKey:
			return added, ErrSubscriptionMembershipDocumentKey
		case dto.CourseLocationDocumentUndecodable:
			return added, ErrSubscriptionMembershipDocumentDecode
		}
		value, _ := item.Lookup("document")
		content, err := dto.PythonB64Decode(value)
		if err != nil {
			return added, ErrSubscriptionMembershipDocumentDecode
		}
		raw, _ := item.Lookup("filename")
		if raw == nil {
			// Document.objects.create(filename=None): NOT NULL, no row.
			return added, ErrSubscriptionMembershipDocumentDecode
		}
		// CharField stores str(value): a str unchanged, a JSON number/bool/
		// list/object its Python repr, an uploaded file its name.
		filename := subscriptionMembershipDocumentName(raw)
		if !dto.AssociateDraftStorable(filename) || utf8.RuneCountInString(filename) > 255 {
			// Document.objects.create(filename=...) fails (NUL, lone surrogates, over 255
			// characters): the 500 page, no row.
			return added, ErrSubscriptionMembershipDocumentDecode
		}
		document := s.uploads.newDocument(filename)
		if _, isText := raw.(string); !isText {
			// os.path.join(storing_path, document.filename) is a TypeError for a value that
			// is not a str: the row and its entry are written, then it raises.
			if err = tx.CreateMembershipDocument(ctx, document, audit, document.Created); err != nil {
				return added, err
			}
			return added, errSubscriptionMembershipDocumentStore
		}
		if _, err = documentkey.UploadName(document.Created, uuid.MustParse(document.ID), filename); err != nil {
			// default_storage.save's validate_file_name rejects the name after the INSERT.
			if err = tx.CreateMembershipDocument(ctx, document, audit, document.Created); err != nil {
				return added, err
			}
			return added, errSubscriptionMembershipDocumentStore
		}
		if err = tx.CreateMembershipDocument(ctx, document, audit, document.Created); err != nil {
			return added, err
		}
		if err = s.uploads.save(ctx, document, content); err != nil {
			return added, err
		}
		if err = tx.LinkMembershipDocument(ctx, membership, document.ID); err != nil {
			return added, err
		}
		added++
	}
	return added, nil
}

func applySubscriptionMembershipWrite(row *domain.SubscriptionMembership, write dto.SubscriptionMembershipWrite) {
	if write.Association.Present && write.Association.ID != nil {
		row.AssociationID = *write.Association.ID
	}
	if write.Associate.Present {
		row.AssociateID = write.Associate.ID
	}
	if write.Payment.Present {
		row.PaymentID = write.Payment.ID
	}
	if write.CreationDate != nil {
		row.CreationDate = write.CreationDate.UTC()
	}
	dates := map[string]**string{"start_date": &row.StartDate, "end_date": &row.EndDate}
	for name, value := range write.Dates {
		*dates[name] = value
	}
	text := map[string]**string{"membership_type": &row.MembershipType, "membership_number": &row.MembershipNumber, "description": &row.Description}
	for name, value := range write.Text {
		*text[name] = value
	}
	if write.PricePresent {
		row.Price = write.Price
	}
}

// subscriptionMembershipAuditText is auditlog's str() of a field value.
func subscriptionMembershipAuditText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func subscriptionMembershipNaiveUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}

// subscriptionMembershipChanges is auditlog's update diff of the instance.
func subscriptionMembershipChanges(old, row domain.SubscriptionMembership) map[string][2]string {
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	text := subscriptionMembershipAuditText
	diff("subscription", text(old.SubscriptionID), text(row.SubscriptionID))
	diff("sport_association", old.AssociationID, row.AssociationID)
	diff("creation_date", subscriptionMembershipNaiveUTC(old.CreationDate), subscriptionMembershipNaiveUTC(row.CreationDate))
	diff("start_date", text(old.StartDate), text(row.StartDate))
	diff("end_date", text(old.EndDate), text(row.EndDate))
	diff("membership_type", text(old.MembershipType), text(row.MembershipType))
	diff("membership_number", text(old.MembershipNumber), text(row.MembershipNumber))
	diff("description", text(old.Description), text(row.Description))
	diff("price", text(old.Price), text(row.Price))
	diff("payment", text(old.PaymentID), text(row.PaymentID))
	diff("associate", text(old.AssociateID), text(row.AssociateID))
	return changes
}

// finish commits the saved membership: requests are not atomic, so a
// document failure after the save keeps it.
func (s *SubscriptionMemberships) finish(ctx context.Context, tx domain.SubscriptionMembershipTransaction, row domain.SubscriptionMembership,
	created bool, failure error) (map[string]any, error) {
	if failure != nil {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, failure
	}
	var paid *bool
	if row.PaymentID != nil {
		value, err := tx.PaymentPaid(ctx, *row.PaymentID)
		if err != nil {
			return nil, err
		}
		paid = value
	}
	documents := "[]"
	if !created {
		var err error
		if documents, err = tx.MembershipDocuments(ctx, row.ID); err != nil {
			return nil, err
		}
	}
	item, err := subscriptionMembershipItem(row, paid, documents)
	if err != nil {
		return nil, err
	}
	return item, tx.Commit()
}

// Add follows SubscriptionMembershipViewSet.add (201); AddAssociation ran first.
func (s *SubscriptionMemberships) Add(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionMembershipRequest, body any,
	form bool, audit domain.LoginAuditContext) (map[string]any, error) {
	// A form body is an immutable QueryDict; with files DRF merges them into a mutable copy.
	if _, ok := body.(domain.RequestObject); !ok || (form && !dto.SubscriptionMembershipHasFile(body)) {
		return nil, ErrSubscriptionMembershipBody
	}
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	write, invalid := dto.ParseSubscriptionMembershipWrite(body, form, false)
	// request.data['sport_association'] replaces any client value.
	delete(invalid, "sport_association")
	if len(invalid) == 0 {
		invalid = nil
	}
	write.Association = dto.SubscriptionMembershipRelation{Present: true, ID: &association}
	if err = s.validate(ctx, tx, association, write, invalid, supplierGroup(request.GroupHeader)); err != nil {
		return nil, err
	}
	if err = s.subscriptionMembershipDocuments(write); err != nil {
		return nil, err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	zero := "0"
	row := domain.SubscriptionMembership{ID: s.ids(), AssociationID: association, CreationDate: at, MembershipNumber: &zero}
	applySubscriptionMembershipWrite(&row, write)
	if err = tx.CreateMembership(ctx, row, paymentCategoryAuditContext(identity, audit), at); err != nil {
		return nil, err
	}
	added, failure := s.handleDocuments(ctx, tx, row.ID, write, paymentCategoryAuditContext(identity, audit))
	return s.finish(ctx, tx, row, added == 0, failure)
}

// Update follows SubscriptionMembershipViewSet.update (partial).
func (s *SubscriptionMemberships) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, request SubscriptionMembershipRequest,
	body any, form bool, audit domain.LoginAuditContext) (map[string]any, error) {
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	current, association, err := s.membership(ctx, tx, identity, raw, request.Query)
	if err != nil {
		return nil, err
	}
	write, invalid := dto.ParseSubscriptionMembershipWrite(body, form, true)
	if err = s.validate(ctx, tx, association, write, invalid, supplierGroup(request.GroupHeader)); err != nil {
		return nil, err
	}
	if err = s.subscriptionMembershipDocuments(write); err != nil {
		return nil, err
	}
	old := current.SubscriptionMembership
	row := old
	applySubscriptionMembershipWrite(&row, write)
	at := s.now().UTC().Truncate(time.Microsecond)
	if err = tx.SaveMembership(ctx, row, subscriptionMembershipChanges(old, row), paymentCategoryAuditContext(identity, audit), at); err != nil {
		return nil, err
	}
	_, failure := s.handleDocuments(ctx, tx, row.ID, write, paymentCategoryAuditContext(identity, audit))
	return s.finish(ctx, tx, row, false, failure)
}

// Delete follows SubscriptionMembershipViewSet.delete (204).
func (s *SubscriptionMemberships) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, request SubscriptionMembershipRequest,
	audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	current, _, err := s.membership(ctx, tx, identity, raw, request.Query)
	if err != nil {
		return err
	}
	if err = tx.DeleteMembership(ctx, current.ID, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

// DeleteWithoutPK is the viewset's delete() on the list and add routes:
// get_object() runs get_queryset() (its errors are 500s too) and then fails
// without a pk.
func (s *SubscriptionMemberships) DeleteWithoutPK(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionMembershipRequest) error {
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, _, err = subscriptionMembershipQueryset(ctx, tx, identity, request.Query); err != nil {
		return err
	}
	return ErrSubscriptionMembershipNoPK
}

// Create is the router list route's stock CreateModelMixin.create: the
// serializer validates the client's sport_association (required; the tenant
// scope applies only with a current association), then perform_create's
// SportAssociation.objects.get(user=request.user) replaces it.
func (s *SubscriptionMemberships) Create(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionMembershipRequest, body any,
	form bool, audit domain.LoginAuditContext) (map[string]any, error) {
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, associationErr := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if associationErr != nil && !errors.Is(associationErr, domain.ErrAssociationNotFound) {
		return nil, associationErr
	}
	write, invalid := dto.ParseSubscriptionMembershipWrite(body, form, false)
	if _, ok := body.(domain.RequestObject); ok && !write.Association.Present && invalid["sport_association"] == nil {
		if invalid == nil {
			invalid = map[string]any{}
		}
		invalid["sport_association"] = []string{dto.PaymentCategoryValidationRequired}
	}
	if len(invalid) > 0 && invalid["non_field_errors"] != nil {
		return nil, &SubscriptionMembershipValidationError{Fields: invalid}
	}
	if err = s.validate(ctx, tx, association, write, invalid, supplierGroup(request.GroupHeader)); err != nil {
		return nil, err
	}
	if associationErr != nil {
		return nil, associationErr
	}
	if err = s.subscriptionMembershipDocuments(write); err != nil {
		return nil, err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	zero := "0"
	row := domain.SubscriptionMembership{ID: s.ids(), CreationDate: at, MembershipNumber: &zero}
	applySubscriptionMembershipWrite(&row, write)
	row.AssociationID = association
	if err = tx.CreateMembership(ctx, row, paymentCategoryAuditContext(identity, audit), at); err != nil {
		return nil, err
	}
	added, failure := s.handleDocuments(ctx, tx, row.ID, write, paymentCategoryAuditContext(identity, audit))
	return s.finish(ctx, tx, row, added == 0, failure)
}

// Retrieve is the router detail route's stock retrieve: get_object() (with
// the subscription and associate_id query filters), then the serializer.
func (s *SubscriptionMemberships) Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string,
	request SubscriptionMembershipRequest) (map[string]any, error) {
	tx, err := s.store.BeginSubscriptionMembership(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	current, _, err := s.membership(ctx, tx, identity, raw, request.Query)
	if err != nil {
		return nil, err
	}
	return subscriptionMembershipItem(current.SubscriptionMembership, current.Paid, current.Documents)
}
