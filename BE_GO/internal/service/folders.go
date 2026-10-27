package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrFolderIDValue is Folder.objects.get(id=value) with a value int() rejects (500).
var ErrFolderIDValue = errors.New("folder id is not an integer")

type Folders struct{ store domain.FolderStore }

func NewFolders(store domain.FolderStore) *Folders { return &Folders{store: store} }

// List follows FolderViewSet.list: SportAssociation.objects.get(user=request.user),
// then the children of ?folder= (an empty value means roots) with the
// ancestor path, or the roots with a null path.
func (s *Folders) List(ctx context.Context, identity domain.ResolvedIdentity, folder string, present bool) (dto.FolderList, error) {
	if err := ctx.Err(); err != nil {
		return dto.FolderList{}, err
	}
	association, err := s.store.ActiveAssociationForUser(ctx, identity.ScopeUser.ID)
	if err != nil {
		return dto.FolderList{}, err
	}
	var parent *int64
	var path []dto.FolderCrumb
	if present && folder != "" {
		id, err := dto.PythonIntPrep(folder)
		if err != nil {
			return dto.FolderList{}, ErrFolderIDValue
		}
		current, err := s.store.FolderByID(ctx, id)
		if err != nil {
			return dto.FolderList{}, err
		}
		if current.AssociationID != association {
			return dto.FolderList{}, domain.ErrFolderForeign
		}
		ancestors, err := s.store.Ancestors(ctx, current)
		if err != nil {
			return dto.FolderList{}, err
		}
		path = make([]dto.FolderCrumb, 0, len(ancestors))
		for _, ancestor := range ancestors {
			path = append(path, dto.FolderCrumb{ID: ancestor.ID, Name: ancestor.Name})
		}
		parent = &current.ID
	}
	rows, err := s.store.AssociationFolders(ctx, association)
	if err != nil {
		return dto.FolderList{}, err
	}
	return dto.FolderList{Data: dto.FolderNodes(rows, parent), Path: path}, nil
}

// ErrFolderLookup is DRF's bare Http404 ("Not found.") for a pk the integer
// field rejects in get_object.
var ErrFolderLookup = errors.New("Not found.")

type FolderDeletes struct{ store domain.FolderDeleteStore }

func NewFolderDeletes(store domain.FolderDeleteStore) *FolderDeletes {
	return &FolderDeletes{store: store}
}

// Delete follows FolderViewSet.delete: get_object on the association's
// folders (the ownership check after it is unreachable), then the MPTT delete.
func (s *FolderDeletes) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, err := s.store.BeginFolderDelete(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return err
	}
	id, err := dto.PythonIntPrep(raw)
	if err != nil {
		return ErrFolderLookup
	}
	if err = tx.DeleteSubtree(ctx, id, association); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

// ErrFolderMoveBody is request.data.get on a non-dict body (500).
var ErrFolderMoveBody = errors.New("folder move body has no get")

type FolderMoves struct{ store domain.FolderMoveStore }

func NewFolderMoves(store domain.FolderMoveStore) *FolderMoves { return &FolderMoves{store: store} }

// Move follows FolderViewSet.move: get_object, then a truthy new_parent is
// Folder.objects.get(id=...) (any association; foreign is 403) and
// move_to(new_parent), otherwise move_to(None).
func (s *FolderMoves) Move(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, err := s.store.BeginFolderMove(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return err
	}
	id, err := dto.PythonIntPrep(raw)
	if err != nil {
		return ErrFolderLookup
	}
	if err = tx.OwnFolder(ctx, id, association); err != nil {
		return err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrFolderMoveBody
	}
	value, _ := object.Lookup("new_parent")
	var target *int64
	if dto.PythonTruthy(value) {
		parsed, err := dto.PythonIntPrep(value)
		if err != nil {
			return ErrFolderIDValue
		}
		target = &parsed
	}
	if err = tx.MoveFolder(ctx, id, association, target); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
