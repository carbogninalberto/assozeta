package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrArchiveQuery stands for the list view's uncaught errors: int() of the
// page, negative slicing, and Folder.objects.get raising (500).
var ErrArchiveQuery = errors.New("document archive list raised")

const archivePageSize = 40

type DocumentArchive struct{ store domain.DocumentArchiveStore }

func NewDocumentArchive(store domain.DocumentArchiveStore) *DocumentArchive {
	return &DocumentArchive{store: store}
}

// List follows DocumentArchiveViewSet.list.
func (s *DocumentArchive) List(ctx context.Context, identity domain.ResolvedIdentity, folder, page string) (dto.ArchiveList, error) {
	if err := ctx.Err(); err != nil {
		return dto.ArchiveList{}, err
	}
	association, err := s.store.ActiveAssociationForUser(ctx, identity.ScopeUser.ID)
	if err != nil {
		return dto.ArchiveList{}, err
	}
	number, err := dto.PythonIntPrep(page)
	if err != nil {
		return dto.ArchiveList{}, ErrArchiveQuery
	}
	var folderID *int64
	if folder != "" {
		id, err := dto.PythonIntPrep(folder)
		if err != nil {
			return dto.ArchiveList{}, ErrArchiveQuery
		}
		current, err := s.store.FolderByID(ctx, id)
		if errors.Is(err, domain.ErrFolderParentNotFound) {
			return dto.ArchiveList{}, ErrArchiveQuery
		}
		if err != nil {
			return dto.ArchiveList{}, err
		}
		if current.AssociationID != association {
			return dto.ArchiveList{}, domain.ErrFolderForeign
		}
		folderID = &current.ID
	}
	// Django rejects negative queryset slicing.
	if number < 0 {
		return dto.ArchiveList{}, ErrArchiveQuery
	}
	start := number * archivePageSize
	rows, total, err := s.store.ArchivedDocuments(ctx, association, folderID, start, archivePageSize)
	if err != nil {
		return dto.ArchiveList{}, err
	}
	list := dto.ArchiveList{Data: make([]dto.ArchiveRow, 0, len(rows)), Pagination: dto.ArchivePagination{Page: number,
		TotalPages: (total + archivePageSize - 1) / archivePageSize, TotalItems: total, HasNext: start+archivePageSize < total}}
	for _, row := range rows {
		list.Data = append(list.Data, dto.ArchiveRowFrom(row))
	}
	return list, nil
}
