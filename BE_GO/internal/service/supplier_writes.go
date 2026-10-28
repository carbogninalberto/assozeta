package service

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type SupplierDeletes struct {
	store domain.SupplierDeleteStore
	now   func() time.Time
}

func NewSupplierDeletes(store domain.SupplierDeleteStore, now func() time.Time) *SupplierDeletes {
	return &SupplierDeletes{store: store, now: now}
}

// ErrSupplierBulkMissing and ErrSupplierBulkInvalid are the bulk delete's 400s.
var (
	ErrSupplierBulkMissing = &supplierBulkError{"No supplier IDs provided."}
	ErrSupplierBulkInvalid = &supplierBulkError{"Invalid UUID format."}
)

type supplierBulkError struct{ Message string }

func (e *supplierBulkError) Error() string { return e.Message }

func (s *SupplierDeletes) remove(ctx context.Context, identity domain.ResolvedIdentity, ids []string, group string, audit domain.LoginAuditContext) (int, error) {
	tx, err := s.store.BeginSupplierDelete(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return 0, err
	}
	found, err := tx.LiveSuppliers(ctx, association, ids, supplierGroup(group))
	if err != nil {
		return 0, err
	}
	auditContext, at := paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)
	for _, id := range found {
		if err = tx.DeleteSupplier(ctx, id, auditContext, at); err != nil {
			return 0, err
		}
	}
	return len(found), tx.Commit()
}

// Delete follows supplier_delete.
func (s *SupplierDeletes) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	deleted, err := s.remove(ctx, identity, []string{id}, group, audit)
	if err != nil {
		return err
	}
	if deleted == 0 {
		return domain.ErrSupplierDeleteDenied
	}
	return nil
}

// BulkDelete follows supplier_bulk_delete, including the logger's
// len(request.data.get('supplier_ids', [])) on a non-dict body or an
// unsized value (a 500 before the role check).
func (s *SupplierDeletes) BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, group string, audit domain.LoginAuditContext) (string, error) {
	object, ok := body.(domain.RequestObject)
	if !ok && body != nil {
		return "", ErrPaymentSaveFailed
	}
	value, present := object.Lookup("supplier_ids")
	var items []any
	switch v := value.(type) {
	case nil:
		if present {
			return "", ErrPaymentSaveFailed
		}
	case []any:
		items = v
	case string:
		for _, character := range v {
			items = append(items, string(character))
		}
	case domain.RequestObject:
		for _, field := range v {
			items = append(items, field.Name)
		}
	default:
		return "", ErrPaymentSaveFailed
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", ErrSupplierForbidden
	}
	if len(items) == 0 {
		// The association lookup precedes the emptiness check.
		tx, err := s.store.BeginSupplierDelete(ctx)
		if err != nil {
			return "", err
		}
		_, err = tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
		tx.Rollback()
		if err != nil {
			return "", err
		}
		return "", ErrSupplierBulkMissing
	}
	ids := make([]string, 0, len(items))
	for _, item := range items {
		text, ok := item.(string)
		if !ok {
			return "", ErrSupplierBulkInvalid
		}
		parsed, err := paymentInfoParseUUID(text)
		if err != nil {
			return "", ErrSupplierBulkInvalid
		}
		ids = append(ids, parsed.String())
	}
	deleted, err := s.remove(ctx, identity, ids, group, audit)
	if err != nil {
		return "", err
	}
	return strconv.Itoa(deleted) + " suppliers deleted.", nil
}

type SupplierWrites struct {
	store domain.SupplierWriteStore
	now   func() time.Time
	ids   func() string
}

func NewSupplierWrites(store domain.SupplierWriteStore, now func() time.Time, ids func() string) *SupplierWrites {
	return &SupplierWrites{store: store, now: now, ids: ids}
}

// ErrSupplierPrimaryKeyChange refuses an update naming a new supplier_id:
// Model.save() would insert a copy under the new key (not ported).
var ErrSupplierPrimaryKeyChange = errors.New("supplier primary key change is not ported")

func supplierTextFields(row *domain.Supplier) map[string]**string {
	return map[string]**string{"address": &row.Address, "tax_code": &row.TaxCode, "vat_number": &row.VATNumber, "type": &row.Type,
		"email": &row.Email, "phone_number": &row.PhoneNumber, "city": &row.City, "cap": &row.Cap, "state": &row.State,
		"country": &row.Country, "nationality": &row.Nationality, "note": &row.Note}
}

// validateReferences applies the related querysets, the tenant scope (a
// PermissionDenied escapes the serializer before its errors are raised) and
// the supplier_id UniqueValidator.
func validateSupplierReferences(ctx context.Context, tx domain.SupplierWriteTransaction, write dto.SupplierWrite, invalid *dto.SupplierValidationError,
	association string, group *string, exclude *string) (*dto.SupplierValidationError, error) {
	if invalid == nil {
		invalid = &dto.SupplierValidationError{}
	}
	if write.SupplierID != nil {
		taken, err := tx.SupplierIDTaken(ctx, *write.SupplierID, group, exclude)
		if err != nil {
			return nil, err
		}
		if taken {
			invalid.Add("supplier_id", dto.SupplierValidationUnique)
		}
	}
	if write.AssociationID != nil {
		exists, err := tx.AssociationExists(ctx, *write.AssociationID)
		if err != nil {
			return nil, err
		}
		if !exists {
			invalid.Add("sport_association", "related-does-not-exist:"+*write.AssociationID)
		} else if *write.AssociationID != association {
			return nil, domain.ErrSupplierForeignReference
		}
	}
	if write.GroupID != nil {
		owner, found, err := tx.GroupAssociation(ctx, *write.GroupID)
		if err != nil {
			return nil, err
		}
		if !found {
			invalid.Add("group", "related-does-not-exist:"+*write.GroupID)
		} else if owner != nil && *owner != association {
			return nil, domain.ErrSupplierForeignReference
		}
	}
	if len(invalid.Fields) == 0 {
		return nil, nil
	}
	return invalid, nil
}

func applySupplierWrite(row *domain.Supplier, write dto.SupplierWrite) {
	fields := supplierTextFields(row)
	for name, value := range write.Text {
		if name == "name" {
			if value != nil {
				row.Name = *value
			}
			continue
		}
		*fields[name] = value
	}
	if write.AssociationPresent {
		row.AssociationID = write.AssociationID
	}
	if write.GroupPresent {
		row.GroupID = write.GroupID
	}
	if write.DeletedPresent {
		row.Deleted = write.Deleted
	}
}

// Add follows supplier_add.
func (s *SupplierWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string,
	audit domain.LoginAuditContext) (*domain.Supplier, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, ErrSupplierForbidden
	}
	tx, err := s.store.BeginSupplierWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	write, invalid, err := dto.ParseSupplierWrite(body, false, form)
	if err != nil {
		return nil, err
	}
	// A body that is not a dict fails before any field is validated.
	if invalid == nil || invalid.Fields[0].Name != "non_field_errors" {
		if invalid, err = validateSupplierReferences(ctx, tx, write, invalid, association, supplierGroup(group), nil); err != nil {
			return nil, err
		}
	}
	if invalid != nil {
		return nil, invalid
	}
	defaultType, country, nationality := "supplier", "Italia", "Italiana"
	row := domain.Supplier{Type: &defaultType, Country: &country, Nationality: &nationality}
	applySupplierWrite(&row, write)
	row.ID = s.ids()
	if write.SupplierID != nil {
		row.ID = *write.SupplierID
	}
	if group != "" {
		if write.GroupPresent {
			// create(group=..., group_id=header) is an unexpected keyword.
			return nil, ErrPaymentSaveFailed
		}
		// GroupAwareManager.create stores the raw header.
		parsed, err := paymentInfoParseUUID(group)
		if err != nil {
			return nil, ErrPaymentSaveFailed
		}
		text := parsed.String()
		row.GroupID = &text
	}
	named := row.AssociationID != nil
	if err = tx.InsertSupplier(ctx, row); err != nil {
		return nil, err
	}
	auditContext, at := paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)
	created := domain.SupplierAudit{ID: row.ID, Action: 0, FullDiff: true, Context: auditContext, At: at,
		Association: &association, Path: "changes_json.sport_association"}
	if !named {
		created.Association, created.Path = nil, "actor.sport_association"
		if created.Association, err = tx.ActiveAssociationOf(ctx, identity.Actor.ID); err != nil {
			return nil, err
		}
	}
	if err = tx.AuditSupplier(ctx, created); err != nil {
		return nil, err
	}
	row.AssociationID = &association
	if err = tx.SaveSupplier(ctx, row); err != nil {
		return nil, err
	}
	saved := domain.SupplierAudit{ID: row.ID, Action: 1, Context: auditContext, At: at, Association: &association, Path: "changes_json.sport_association"}
	switch {
	case row.Deleted:
		// The soft-delete manager cannot load the deleted row for the diff.
		saved.FullDiff = true
	case !named:
		saved.Changes = map[string][2]string{"sport_association": {"None", association}}
	default:
		saved.ID = ""
	}
	if saved.ID != "" {
		if err = tx.AuditSupplier(ctx, saved); err != nil {
			return nil, err
		}
	}
	return &row, tx.Commit()
}

func supplierValue(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// Update follows supplier_update.
func (s *SupplierWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string,
	audit domain.LoginAuditContext) (*domain.Supplier, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginSupplierWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	row, err := tx.LiveSupplier(ctx, id, supplierGroup(group))
	if err != nil {
		return nil, err
	}
	if row == nil || row.AssociationID == nil {
		// supplier.sport_association on None (or a supplier without one).
		return nil, ErrPaymentSaveFailed
	}
	if *row.AssociationID != association {
		return nil, domain.ErrSupplierDeleteDenied
	}
	write, invalid, err := dto.ParseSupplierWrite(body, true, form)
	if err != nil {
		return nil, err
	}
	if invalid == nil || invalid.Fields[0].Name != "non_field_errors" {
		if invalid, err = validateSupplierReferences(ctx, tx, write, invalid, association, supplierGroup(group), &row.ID); err != nil {
			return nil, err
		}
	}
	if invalid != nil {
		return nil, ErrSupplierUpdateInvalid
	}
	if write.SupplierID != nil && *write.SupplierID != row.ID {
		return nil, ErrSupplierPrimaryKeyChange
	}
	old := *row
	applySupplierWrite(row, write)
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	diff("name", old.Name, row.Name)
	oldText, newText := supplierTextFields(&old), supplierTextFields(row)
	for name := range oldText {
		diff(name, supplierValue(*oldText[name]), supplierValue(*newText[name]))
	}
	diff("sport_association", supplierValue(old.AssociationID), supplierValue(row.AssociationID))
	diff("group", supplierValue(old.GroupID), supplierValue(row.GroupID))
	diff("deleted", pythonBool(old.Deleted), pythonBool(row.Deleted))
	if err = tx.SaveSupplier(ctx, *row); err != nil {
		return nil, err
	}
	if len(changes) > 0 {
		if err = tx.AuditSupplier(ctx, domain.SupplierAudit{ID: row.ID, Action: 1, Changes: changes, Association: &association, Path: "direct",
			Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
			return nil, err
		}
	}
	return row, tx.Commit()
}

// ErrSupplierUpdateInvalid is {"msg": "validation errors."}.
var ErrSupplierUpdateInvalid = errors.New("supplier update validation errors")
