package service

import (
	"context"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
