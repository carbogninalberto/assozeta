package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// FolderValidationError is FolderSerializer's 400: field -> message codes.
type FolderValidationError struct{ Fields map[string][]string }

func (e *FolderValidationError) Error() string { return "invalid folder input" }

// FolderBody is the request body as FolderViewSet sees request.data: Mutable
// is false for a non-dict JSON body and for an immutable QueryDict (a form
// body, or a multipart body without files); Form selects DRF's HTML input rules.
type FolderBody struct {
	Value   any
	Err     error
	Form    bool
	Mutable bool
}

type FolderWrites struct {
	store domain.FolderWriteStore
	now   func() time.Time
}

func NewFolderWrites(store domain.FolderWriteStore, now func() time.Time) *FolderWrites {
	return &FolderWrites{store: store, now: now}
}

// Add follows FolderViewSet.add: SportAssociation.objects.get(user=...)
// before request.data, the sport_association assignment (a 500 unless the
// data is a mutable dict), FolderSerializer validation, the unique-together
// check (skipped for a None parent) and the MPTT insert as last child.
func (s *FolderWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body FolderBody) (dto.FolderNode, error) {
	if err := ctx.Err(); err != nil {
		return dto.FolderNode{}, err
	}
	tx, err := s.store.BeginFolderWrite(ctx)
	if err != nil {
		return dto.FolderNode{}, err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if body.Err != nil {
		return dto.FolderNode{}, body.Err
	}
	object, ok := body.Value.(domain.RequestObject)
	if !ok || !body.Mutable {
		return dto.FolderNode{}, domain.ErrFolderAddBody
	}
	write, invalid, err := dto.ParseFolderWrite(object, false, body.Form)
	if err != nil {
		return dto.FolderNode{}, err
	}
	parent, err := s.related(ctx, tx, write, association, invalid)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if len(invalid) > 0 {
		return dto.FolderNode{}, &FolderValidationError{Fields: invalid}
	}
	if parent != nil {
		taken, err := tx.FolderNameTaken(ctx, *write.Name, parent, association, nil)
		if err != nil {
			return dto.FolderNode{}, err
		}
		if taken {
			return dto.FolderNode{}, &FolderValidationError{Fields: map[string][]string{"non_field_errors": {dto.FolderValidationUnique}}}
		}
	}
	createdAt := s.now().UTC().Truncate(time.Microsecond)
	if write.CreatedAt != nil {
		createdAt = *write.CreatedAt
	}
	folder, err := tx.InsertFolder(ctx, *write.Name, parent, association, createdAt)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if err = ctx.Err(); err != nil {
		return dto.FolderNode{}, err
	}
	if err = tx.Commit(); err != nil {
		return dto.FolderNode{}, err
	}
	return dto.FolderNodeOf(folder, nil), nil
}

// Update follows FolderViewSet.update: get_object() (association lookup,
// then the pk) before request.data, partial FolderSerializer validation, the
// unique-together check on changed values (missing ones taken from the
// instance; an unchanged None parent filters IS NULL; a None among the
// changed values skips it), then MPTTModel.save and the nested response.
func (s *FolderWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body FolderBody) (dto.FolderNode, error) {
	if err := ctx.Err(); err != nil {
		return dto.FolderNode{}, err
	}
	tx, err := s.store.BeginFolderWrite(ctx)
	if err != nil {
		return dto.FolderNode{}, err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return dto.FolderNode{}, err
	}
	pk, ok := dto.FolderLookupPK(raw)
	if !ok {
		return dto.FolderNode{}, ErrFolderLookup
	}
	if pk.Missing {
		return dto.FolderNode{}, domain.ErrFolderNotFound
	}
	node, err := tx.OwnedFolder(ctx, pk.ID, association)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if body.Err != nil {
		return dto.FolderNode{}, body.Err
	}
	if body.Value == nil {
		// Serializer(data=None): "No data provided".
		return dto.FolderNode{}, &FolderValidationError{Fields: map[string][]string{"non_field_errors": {"no-data"}}}
	}
	object, ok := body.Value.(domain.RequestObject)
	if !ok {
		return dto.FolderNode{}, &FolderValidationError{Fields: map[string][]string{"non_field_errors": {"dictionary:" + dto.FolderBodyType(body.Value)}}}
	}
	write, invalid, err := dto.ParseFolderWrite(object, true, body.Form)
	if err != nil {
		return dto.FolderNode{}, err
	}
	parent, err := s.related(ctx, tx, write, association, invalid)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if write.AssociationPresent && invalid["sport_association"] == nil {
		exists, err := tx.AssociationExists(ctx, write.Association)
		if err != nil {
			return dto.FolderNode{}, err
		}
		if !exists {
			invalid["sport_association"] = []string{"related-does-not-exist:" + dto.PythonStr(write.AssociationRaw)}
		} else if write.Association != association {
			return dto.FolderNode{}, domain.ErrFolderForeignReference
		}
	}
	if len(invalid) > 0 {
		return dto.FolderNode{}, &FolderValidationError{Fields: invalid}
	}
	name := node.Name
	if write.Name != nil {
		name = *write.Name
	}
	if !write.ParentPresent {
		parent = node.ParentID
	}
	parentChanged := !sameFolder(parent, node.ParentID)
	// The association can only stay the owner's: a foreign one is refused above.
	var changed []*int64
	nameChanged := name != node.Name
	if parentChanged {
		changed = append(changed, parent)
	}
	if nameChanged || parentChanged {
		hasNone := false
		for _, value := range changed {
			hasNone = hasNone || value == nil
		}
		if !hasNone {
			taken, err := tx.FolderNameTaken(ctx, name, parent, association, &node.ID)
			if err != nil {
				return dto.FolderNode{}, err
			}
			if taken {
				return dto.FolderNode{}, &FolderValidationError{Fields: map[string][]string{"non_field_errors": {dto.FolderValidationUnique}}}
			}
		}
	}
	createdAt := node.CreatedAt
	if write.CreatedAt != nil {
		createdAt = *write.CreatedAt
	}
	saved, err := tx.SaveFolder(ctx, node.ID, name, parent, parentChanged, association, createdAt)
	if err != nil {
		return dto.FolderNode{}, err
	}
	descendants, err := tx.Descendants(ctx, saved)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if err = ctx.Err(); err != nil {
		return dto.FolderNode{}, err
	}
	if err = tx.Commit(); err != nil {
		return dto.FolderNode{}, err
	}
	return dto.FolderNodeOf(saved, descendants), nil
}

// related resolves the parent field: an unknown pk is a field error, a
// folder of another association is tenant_scope's 403 (escaping the
// collected field errors). It returns the parent id (nil for None).
func (s *FolderWrites) related(ctx context.Context, tx domain.FolderWriteTransaction, write dto.FolderWrite, association string,
	invalid map[string][]string) (*int64, error) {
	if write.Parent == nil {
		return nil, nil
	}
	missing := func() (*int64, error) {
		invalid["parent"] = []string{"related-does-not-exist:" + dto.PythonStr(write.Parent.Raw)}
		return nil, nil
	}
	if write.Parent.Missing {
		return missing()
	}
	folder, err := tx.FindFolder(ctx, write.Parent.ID)
	if err != nil {
		return nil, err
	}
	if folder == nil {
		return missing()
	}
	// An empty association is no current tenant (tenant_scope does not enforce).
	if association != "" && folder.AssociationID != association {
		return nil, domain.ErrFolderForeignReference
	}
	return &folder.ID, nil
}

func sameFolder(left, right *int64) bool {
	if left == nil || right == nil {
		return left == right
	}
	return *left == *right
}

// Create is the router list route's stock CreateModelMixin.create: request.data
// is not written, so the serializer validates the client's sport_association
// (required; the tenant scope applies only with a current association) and
// any dict or QueryDict body; the unique-together check (skipped for a None
// parent) uses that association; perform_create's
// SportAssociation.objects.get(user=request.user) then replaces it.
func (s *FolderWrites) Create(ctx context.Context, identity domain.ResolvedIdentity, body FolderBody) (dto.FolderNode, error) {
	if err := ctx.Err(); err != nil {
		return dto.FolderNode{}, err
	}
	tx, err := s.store.BeginFolderWrite(ctx)
	if err != nil {
		return dto.FolderNode{}, err
	}
	defer tx.Rollback()
	tenant, associationErr := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if associationErr != nil && !errors.Is(associationErr, domain.ErrAssociationNotFound) {
		return dto.FolderNode{}, associationErr
	}
	if body.Err != nil {
		return dto.FolderNode{}, body.Err
	}
	object, ok := body.Value.(domain.RequestObject)
	if !ok && body.Value != nil {
		return dto.FolderNode{}, &FolderValidationError{Fields: map[string][]string{"non_field_errors": {"dictionary:" + dto.FolderBodyType(body.Value)}}}
	}
	write, invalid, err := dto.ParseFolderWrite(object, false, body.Form)
	if err != nil {
		return dto.FolderNode{}, err
	}
	related, relatedInvalid, err := dto.ParseFolderWrite(object, true, body.Form)
	if err != nil {
		return dto.FolderNode{}, err
	}
	write.AssociationPresent, write.Association, write.AssociationRaw = related.AssociationPresent, related.Association, related.AssociationRaw
	if codes := relatedInvalid["sport_association"]; codes != nil {
		invalid["sport_association"] = codes
	} else if !write.AssociationPresent {
		invalid["sport_association"] = []string{dto.PaymentCategoryValidationRequired}
	}
	parent, err := s.related(ctx, tx, write, tenant, invalid)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if write.AssociationPresent && invalid["sport_association"] == nil {
		exists, err := tx.AssociationExists(ctx, write.Association)
		if err != nil {
			return dto.FolderNode{}, err
		}
		if !exists {
			invalid["sport_association"] = []string{"related-does-not-exist:" + dto.PythonStr(write.AssociationRaw)}
		} else if tenant != "" && write.Association != tenant {
			return dto.FolderNode{}, domain.ErrFolderForeignReference
		}
	}
	if len(invalid) > 0 {
		return dto.FolderNode{}, &FolderValidationError{Fields: invalid}
	}
	if parent != nil {
		taken, err := tx.FolderNameTaken(ctx, *write.Name, parent, write.Association, nil)
		if err != nil {
			return dto.FolderNode{}, err
		}
		if taken {
			return dto.FolderNode{}, &FolderValidationError{Fields: map[string][]string{"non_field_errors": {dto.FolderValidationUnique}}}
		}
	}
	if associationErr != nil {
		return dto.FolderNode{}, associationErr
	}
	createdAt := s.now().UTC().Truncate(time.Microsecond)
	if write.CreatedAt != nil {
		createdAt = *write.CreatedAt
	}
	folder, err := tx.InsertFolder(ctx, *write.Name, parent, tenant, createdAt)
	if err != nil {
		return dto.FolderNode{}, err
	}
	if err = ctx.Err(); err != nil {
		return dto.FolderNode{}, err
	}
	if err = tx.Commit(); err != nil {
		return dto.FolderNode{}, err
	}
	return dto.FolderNodeOf(folder, nil), nil
}

// Retrieve is the router detail route's stock retrieve: get_object() over the
// association's folders, then FolderSerializer with the nested children.
func (s *FolderWrites) Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) (dto.FolderNode, error) {
	tx, err := s.store.BeginFolderWrite(ctx)
	if err != nil {
		return dto.FolderNode{}, err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return dto.FolderNode{}, err
	}
	pk, ok := dto.FolderLookupPK(raw)
	if !ok {
		return dto.FolderNode{}, ErrFolderLookup
	}
	if pk.Missing {
		return dto.FolderNode{}, domain.ErrFolderNotFound
	}
	node, err := tx.OwnedFolder(ctx, pk.ID, association)
	if err != nil {
		return dto.FolderNode{}, err
	}
	descendants, err := tx.Descendants(ctx, node)
	if err != nil {
		return dto.FolderNode{}, err
	}
	return dto.FolderNodeOf(node, descendants), nil
}
