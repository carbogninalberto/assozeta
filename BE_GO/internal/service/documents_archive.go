package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

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

// ErrArchiveLookup is DRF's bare Http404 for a pk the UUID field rejects.
var ErrArchiveLookup = errors.New("Not found.")

// ErrArchiveBody is request.data.get on a non-dict JSON body (500).
var ErrArchiveBody = errors.New("archive body has no get")

type ArchiveWrites struct {
	store domain.ArchiveWriteStore
	now   func() time.Time
}

func NewArchiveWrites(store domain.ArchiveWriteStore, now func() time.Time) *ArchiveWrites {
	return &ArchiveWrites{store: store, now: now}
}

func (s *ArchiveWrites) begin(ctx context.Context, identity domain.ResolvedIdentity, raw string) (domain.ArchiveWriteTransaction, string, string, *int64, error) {
	tx, err := s.store.BeginArchiveWrite(ctx)
	if err != nil {
		return nil, "", "", nil, err
	}
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err == nil {
		var id interface{ String() string }
		if id, err = paymentInfoParseUUID(raw); err != nil {
			err = ErrArchiveLookup
		} else {
			raw = id.String()
		}
	}
	var folder *int64
	if err == nil {
		folder, err = tx.ArchiveFolder(ctx, raw, association)
	}
	if err != nil {
		_ = tx.Rollback()
		return nil, "", "", nil, err
	}
	return tx, association, raw, folder, nil
}

// Move follows DocumentArchiveViewSet.move_to_folder: get_object, then the raw
// folder value assigned to folder_id and saved. auditlog compares the raw
// value with the stored integer (strings_only keeps numbers and None), so an
// equal string ID still records a change.
func (s *ArchiveWrites) Move(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, association, id, current, err := s.begin(ctx, identity, raw)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrArchiveBody
	}
	value, _ := object.Lookup("folder")
	var folder *int64
	if dto.PythonTruthy(value) {
		parsed, err := dto.PythonIntPrep(value)
		if err != nil {
			return ErrArchiveQuery
		}
		owner, err := tx.FolderAssociation(ctx, parsed)
		if err != nil {
			return err
		}
		if owner != association {
			return domain.ErrFolderTargetForeign
		}
		folder = &parsed
	} else {
		value = nil
	}
	old := "None"
	if current != nil {
		old = strconv.FormatInt(*current, 10)
	}
	if !archiveFolderEqual(current, value) {
		rendered := "None"
		if value != nil {
			rendered = dto.PythonStr(value)
		}
		if err = tx.AppendArchiveAudit(ctx, domain.ArchiveMoveAudit{ArchiveID: id, AssociationID: association,
			Changes: map[string][2]string{"folder": {old, rendered}}, Context: paymentCategoryAuditContext(identity, audit),
			At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
			return err
		}
	}
	if err = tx.SetArchiveFolder(ctx, id, folder); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

// archiveFolderEqual is Python == between the stored folder id (or None) and
// the raw request value: numbers compare numerically, strings never equal ints.
func archiveFolderEqual(current *int64, value any) bool {
	if current == nil || value == nil {
		return current == nil && value == nil
	}
	switch v := value.(type) {
	case bool:
		return (v && *current == 1) || (!v && *current == 0)
	case json.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		return err == nil && number == float64(*current)
	}
	return false
}

// Delete follows DocumentArchiveViewSet.delete: get_object, then instance.delete().
func (s *ArchiveWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, association, id, _, err := s.begin(ctx, identity, raw)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.DeleteArchive(ctx, id, association, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
