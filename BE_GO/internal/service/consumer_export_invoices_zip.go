package service

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzip"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// ConsumerExportInvoicesZipTask is application.tasks.export_invoices_to_zip's Celery name.
const ConsumerExportInvoicesZipTask = "export_invoices_to_zip"

// ConsumerExportZipStore is the database side of the task.
type ConsumerExportZipStore interface {
	CreateDocument(ctx context.Context, document domain.NewDocument, at time.Time) error
	ActiveAssociation(ctx context.Context, id uuid.UUID) (bool, error)
	CreateArchive(ctx context.Context, row domain.ArchiveRecord, created, at time.Time) error
	Documents(ctx context.Context, ids []uuid.UUID) ([]repository.ConsumerExportDocument, error)
}

// ConsumerExportInvoicesZip runs export_invoices_to_zip(files=None,
// sport_association=None). The whole body is inside `except Exception`, so
// the task always succeeds with None and stops at the first error:
//
//  1. files None: return;
//  2. Document.objects.filter(document_id__in=files) (a value UUIDField
//     rejects raises here, before any write);
//  3. the archive name <Europe/Rome %Y-%m-%d_%H-%M>_ricevute.zip and
//     Document.objects.create(filename=...);
//  4. SportAssociation.objects.filter(pk=sport_association).first() and
//     SportAssociationDocumentsArchive.objects.create(...) (no association:
//     the NOT NULL insert fails and the Document stays alone);
//  5. PrintingService.download_multiple_files: one writestr per source
//     document in query order, "<idx+1>_<filename>", skipping a document whose
//     object cannot be read (the index still advances); then
//     default_storage.save(<timestamp>/<id>/<filename>, zip) (an error is
//     swallowed there too).
//
// Handed off before any effect: arguments outside lists of UUIDs/strings and
// None/UUID/string associations, and strings that only Python's int()
// would accept as hexadecimal.
type ConsumerExportInvoicesZip struct {
	store     ConsumerExportZipStore
	objects   ConsumerStorageObjects
	overwrite bool
	now       func() time.Time
	ids       func() string
	rome      *time.Location
}

func NewConsumerExportInvoicesZip(store ConsumerExportZipStore, objects ConsumerStorageObjects, overwrite bool, now func() time.Time,
	ids func() string) (*ConsumerExportInvoicesZip, error) {
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		return nil, err
	}
	return &ConsumerExportInvoicesZip{store: store, objects: objects, overwrite: overwrite, now: now, ids: ids, rome: rome}, nil
}

func (s *ConsumerExportInvoicesZip) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.plan, Run: s.run}
}

type consumerExportCall struct {
	skip               bool // files is None, or the filter raises
	files              []uuid.UUID
	association        *uuid.UUID
	associationInvalid bool
}

// consumerPythonUUID is uuid.UUID(hex=value) as UUIDField.to_python calls it:
// 'urn:' and 'uuid:' removed anywhere, braces stripped, hyphens removed, 32
// hexadecimal digits. ok=false is a string Python rejects; err is a 32-character
// string with other characters that int(hex, 16) might still accept.
func consumerPythonUUID(value string) (id uuid.UUID, ok bool, err error) {
	hex := strings.ReplaceAll(strings.ReplaceAll(value, "urn:", ""), "uuid:", "")
	hex = strings.ReplaceAll(strings.Trim(hex, "{}"), "-", "")
	if utf8.RuneCountInString(hex) != 32 {
		return uuid.UUID{}, false, nil
	}
	for _, r := range hex {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F') {
			return uuid.UUID{}, false, celeryworker.ErrHandoff
		}
	}
	id, parseErr := uuid.Parse(hex)
	if parseErr != nil {
		return uuid.UUID{}, false, celeryworker.ErrHandoff
	}
	return id, true, nil
}

func (s *ConsumerExportInvoicesZip) plan(_ context.Context, request *celeryworker.Request) error {
	values, err := consumerPrintBind([]string{"files", "sport_association"}, 0, request)
	if err != nil {
		return err
	}
	call := consumerExportCall{}
	switch files := values["files"].(type) {
	case nil:
		call.skip = true
	case []any:
		for _, item := range files {
			switch v := item.(type) {
			case uuid.UUID:
				call.files = append(call.files, v)
			case string:
				id, ok, err := consumerPythonUUID(v)
				if err != nil {
					return err
				}
				if !ok {
					call.skip = true
				}
				call.files = append(call.files, id)
			default:
				return celeryworker.ErrHandoff
			}
		}
	default:
		return celeryworker.ErrHandoff
	}
	switch v := values["sport_association"].(type) {
	case nil:
	case uuid.UUID:
		call.association = &v
	case string:
		id, ok, err := consumerPythonUUID(v)
		if err != nil {
			return err
		}
		if ok {
			call.association = &id
		} else {
			call.associationInvalid = true
		}
	default:
		return celeryworker.ErrHandoff
	}
	request.State = call
	return nil
}

func (s *ConsumerExportInvoicesZip) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, _ := request.State.(consumerExportCall)
	if call.skip {
		return nil, nil
	}
	filename := s.now().In(s.rome).Format("2006-01-02_15-04") + "_ricevute.zip"
	created := s.now().UTC().Truncate(time.Microsecond)
	document := domain.NewDocument{ID: s.ids(), Token: s.ids(), Filename: filename, Created: created}
	if err := s.store.CreateDocument(ctx, document, s.now()); err != nil {
		return nil, err
	}
	if call.associationInvalid {
		return nil, nil
	}
	found := false
	if call.association != nil {
		var err error
		if found, err = s.store.ActiveAssociation(ctx, *call.association); err != nil {
			return nil, err
		}
	}
	if !found {
		return nil, nil // the archive's NOT NULL sport_association insert fails
	}
	archiveAt := s.now()
	if err := s.store.CreateArchive(ctx, domain.ArchiveRecord{ID: s.ids(), Association: call.association.String(), Document: document.ID},
		archiveAt, s.now()); err != nil {
		return nil, err
	}
	name, err := documentkey.UploadName(created, uuid.MustParse(document.ID), filename)
	if err != nil {
		return nil, err
	}
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return nil, err
	}
	documents, err := s.store.Documents(ctx, call.files)
	if err != nil {
		return nil, err
	}
	var archive pyzip.Writer
	for idx, source := range documents {
		stored, err := documentkey.StoredName(source.Created, source.ID, source.Filename, source.Filepath)
		if err != nil {
			continue
		}
		sourceKey, err := documentkey.S3Key(stored, s.objects.Location())
		if err != nil {
			continue
		}
		content, err := s.objects.Read(ctx, sourceKey)
		if err != nil {
			continue
		}
		if err = archive.WriteStr(strconv.Itoa(idx+1)+"_"+source.Filename, content, s.now().UTC()); err != nil {
			return nil, err
		}
	}
	body, err := archive.Close()
	if err != nil {
		return nil, err
	}
	if !s.overwrite {
		exists, err := s.objects.Exists(ctx, key)
		if err != nil {
			return nil, nil // swallowed by download_multiple_files
		}
		if exists {
			return nil, errConsumerExportNameTaken
		}
	}
	kind, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return nil, err
	}
	if !known {
		kind = "application/octet-stream"
	}
	_ = s.objects.Put(ctx, key, body, kind, encoding) // an error is swallowed by download_multiple_files
	return nil, nil
}

// errConsumerExportNameTaken: the archive key (a fresh document id) already
// exists; get_available_name would pick a random alternative name.
var errConsumerExportNameTaken = errors.New("export archive name already taken")
