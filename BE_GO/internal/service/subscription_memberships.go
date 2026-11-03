package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type SubscriptionMemberships struct {
	store domain.SubscriptionMembershipStore
	now   func() time.Time
	ids   func() string
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
	// ErrSubscriptionMembershipUploadUnsupported refuses documents_data that
	// would reach Document creation and the storage upload (not ported)
	// before any write.
	ErrSubscriptionMembershipUploadUnsupported = errors.New("subscription membership document upload is not ported")
	// ErrSubscriptionMembershipPrimaryKeyChange refuses a new
	// subscription_membership_id on update: Model.save() would insert a copy
	// under the new key (not ported).
	ErrSubscriptionMembershipPrimaryKeyChange = errors.New("subscription membership primary key change is not ported")
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

// validate applies the UniqueValidator and the related querysets and tenant
// scope (its PermissionDenied escapes the serializer before the field errors).
func (s *SubscriptionMemberships) validate(ctx context.Context, tx domain.SubscriptionMembershipTransaction, association string,
	write dto.SubscriptionMembershipWrite, invalid map[string]any, exclude *string, group *string) error {
	if invalid == nil {
		invalid = map[string]any{}
	}
	if write.ID != nil {
		taken, err := tx.MembershipIDTaken(ctx, *write.ID, exclude)
		if err != nil {
			return err
		}
		if taken {
			invalid["subscription_membership_id"] = []string{dto.SubscriptionMembershipUnique}
		}
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
		} else if found.AssociationID != nil && *found.AssociationID != association {
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

// subscriptionMembershipDocuments walks _handle_documents without writing.
// The first item that raises decides the failure returned after the save; an
// item that would upload first is refused before any write.
func subscriptionMembershipDocuments(write dto.SubscriptionMembershipWrite) (error, error) {
	if write.FormDocuments {
		return nil, ErrSubscriptionMembershipUploadUnsupported
	}
	for _, item := range write.Documents {
		switch dto.CourseLocationDocument(item) {
		case dto.CourseLocationDocumentMissingKey:
			return ErrSubscriptionMembershipDocumentKey, nil
		case dto.CourseLocationDocumentUndecodable:
			return ErrSubscriptionMembershipDocumentDecode, nil
		default:
			return nil, ErrSubscriptionMembershipUploadUnsupported
		}
	}
	return nil, nil
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
	if _, ok := body.(domain.RequestObject); !ok || form {
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
	write, invalid := dto.ParseSubscriptionMembershipWrite(body, false)
	// request.data['sport_association'] replaces any client value.
	delete(invalid, "sport_association")
	if len(invalid) == 0 {
		invalid = nil
	}
	write.Association = dto.SubscriptionMembershipRelation{Present: true, ID: &association}
	if err = s.validate(ctx, tx, association, write, invalid, nil, supplierGroup(request.GroupHeader)); err != nil {
		return nil, err
	}
	failure, refused := subscriptionMembershipDocuments(write)
	if refused != nil {
		return nil, refused
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	zero := "0"
	row := domain.SubscriptionMembership{ID: s.ids(), AssociationID: association, CreationDate: at, MembershipNumber: &zero}
	if write.ID != nil {
		row.ID = *write.ID
	}
	applySubscriptionMembershipWrite(&row, write)
	if err = tx.CreateMembership(ctx, row, paymentCategoryAuditContext(identity, audit), at); err != nil {
		return nil, err
	}
	return s.finish(ctx, tx, row, true, failure)
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
	write, invalid := dto.ParseSubscriptionMembershipWrite(body, form)
	if err = s.validate(ctx, tx, association, write, invalid, &current.ID, supplierGroup(request.GroupHeader)); err != nil {
		return nil, err
	}
	if write.ID != nil && *write.ID != current.ID {
		return nil, ErrSubscriptionMembershipPrimaryKeyChange
	}
	failure, refused := subscriptionMembershipDocuments(write)
	if refused != nil {
		return nil, refused
	}
	old := current.SubscriptionMembership
	row := old
	applySubscriptionMembershipWrite(&row, write)
	at := s.now().UTC().Truncate(time.Microsecond)
	if err = tx.SaveMembership(ctx, row, subscriptionMembershipChanges(old, row), paymentCategoryAuditContext(identity, audit), at); err != nil {
		return nil, err
	}
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
