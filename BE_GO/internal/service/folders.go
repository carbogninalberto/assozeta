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
