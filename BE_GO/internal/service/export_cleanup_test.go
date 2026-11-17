package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type exportCleanupTestStore struct {
	associations []string
	exports      map[string][]domain.ExportArchive
	documentErr  map[string]error
	archives     []string
	documents    []string
	result       json.RawMessage
}

func (s *exportCleanupTestStore) BeginExportCleanup(context.Context, domain.Job) (domain.ExportCleanupTransaction, error) {
	return &exportCleanupTestTransaction{store: s}, nil
}

type exportCleanupTestTransaction struct{ store *exportCleanupTestStore }

func (t *exportCleanupTestTransaction) ExportAssociations(context.Context) ([]string, error) {
	return t.store.associations, nil
}

func (t *exportCleanupTestTransaction) AssociationExports(_ context.Context, association string, offset int) ([]domain.ExportArchive, error) {
	exports := t.store.exports[association]
	if offset >= len(exports) {
		return nil, nil
	}
	return exports[offset:], nil
}

func (t *exportCleanupTestTransaction) DeleteExportArchive(_ context.Context, archive, _ string, _ time.Time) error {
	t.store.archives = append(t.store.archives, archive)
	return nil
}

func (t *exportCleanupTestTransaction) DeleteExportDocument(_ context.Context, document string, _ time.Time) error {
	t.store.documents = append(t.store.documents, document)
	return t.store.documentErr[document]
}

func (t *exportCleanupTestTransaction) CompleteJob(_ context.Context, _ uuid.UUID, _ uuid.UUID, result json.RawMessage) error {
	t.store.result = result
	return nil
}

func (*exportCleanupTestTransaction) Commit() error   { return nil }
func (*exportCleanupTestTransaction) Rollback() error { return nil }

type exportCleanupTestObjects struct {
	location string
	deleted  []string
	err      error
}

func (o *exportCleanupTestObjects) Location() string { return o.location }
func (o *exportCleanupTestObjects) Delete(_ context.Context, key string) error {
	o.deleted = append(o.deleted, key)
	return o.err
}

// A failing document Collector runs in its own transaction: the archive row is
// already deleted, the document and its cascade are untouched, the export does
// not count, and the age pass continues with the next export.
func TestExportCleanupContinuesAfterDocumentCollectorFailure(t *testing.T) {
	now := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)
	association := "00000000-0000-4000-8000-000000000101"
	store := &exportCleanupTestStore{
		associations: []string{association},
		exports: map[string][]domain.ExportArchive{association: {
			{ArchiveID: "00000000-0000-4000-8000-000000000201", DocumentID: "00000000-0000-4000-8000-000000000301",
				Created: now.Add(-40 * 24 * time.Hour)},
			{ArchiveID: "00000000-0000-4000-8000-000000000202", DocumentID: "00000000-0000-4000-8000-000000000302",
				Created: now.Add(-31 * 24 * time.Hour)},
		}},
		documentErr: map[string]error{"00000000-0000-4000-8000-000000000301": errors.New("protected reference")},
	}
	objects := &exportCleanupTestObjects{location: "private"}
	cleanup := NewExportCleanup(store, objects, func() time.Time { return now },
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	job := domain.Job{ID: uuid.New(), LeaseToken: uuid.New(), State: "running", Kind: domain.JobCleanupOldExports,
		Version: 1, Payload: json.RawMessage(`{}`)}

	if err := cleanup.Run(context.Background(), job); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(store.archives) != 2 || store.archives[0] != "00000000-0000-4000-8000-000000000201" ||
		store.archives[1] != "00000000-0000-4000-8000-000000000202" {
		t.Fatalf("archive deletes %v", store.archives)
	}
	if len(store.documents) != 2 || store.documents[0] != "00000000-0000-4000-8000-000000000301" ||
		store.documents[1] != "00000000-0000-4000-8000-000000000302" {
		t.Fatalf("document deletes %v", store.documents)
	}
	if string(store.result) != `{"deleted_count": 1}` {
		t.Fatalf("result %s", store.result)
	}
}
