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

type orphanMedicalTestStore struct {
	batch       domain.OrphanMedicalBatch
	documentErr map[string]error
	documents   []string
	audited     []string
	completed   bool
}

func (s *orphanMedicalTestStore) BeginOrphanMedical(context.Context, domain.Job) (domain.OrphanMedicalTransaction, error) {
	return &orphanMedicalTestTransaction{store: s}, nil
}

type orphanMedicalTestTransaction struct{ store *orphanMedicalTestStore }

func (t *orphanMedicalTestTransaction) OrphanMedicalCertificates(context.Context) (domain.OrphanMedicalBatch, error) {
	return t.store.batch, nil
}

func (t *orphanMedicalTestTransaction) DeleteOrphanMedicalDocument(_ context.Context, medical, document string, _ time.Time) (map[string][2]string, error) {
	t.store.documents = append(t.store.documents, medical)
	if err := t.store.documentErr[medical]; err != nil {
		return nil, err
	}
	return map[string][2]string{"document": {document, "None"}}, nil
}

func (t *orphanMedicalTestTransaction) AuditOrphanMedicalDelete(_ context.Context, medical string, _ map[string][2]string, _ time.Time) error {
	t.store.audited = append(t.store.audited, medical)
	return nil
}

func (t *orphanMedicalTestTransaction) CompleteJob(context.Context, uuid.UUID, uuid.UUID) error {
	t.store.completed = true
	return nil
}

func (*orphanMedicalTestTransaction) Commit() error   { return nil }
func (*orphanMedicalTestTransaction) Rollback() error { return nil }

type orphanMedicalTestObjects struct {
	location string
	deleted  []string
}

func (o *orphanMedicalTestObjects) Location() string { return o.location }
func (o *orphanMedicalTestObjects) Delete(_ context.Context, key string) error {
	o.deleted = append(o.deleted, key)
	return nil
}

func orphanMedicalTestCertificate(id string) domain.OrphanMedicalCertificate {
	created := time.Date(2026, 11, 10, 8, 0, 0, 0, time.UTC)
	filename := "certificato.pdf"
	return domain.OrphanMedicalCertificate{MedicalID: id, DocumentID: &id, Created: &created, Filename: &filename}
}

func orphanMedicalTestJob() domain.Job {
	return domain.Job{ID: uuid.New(), LeaseToken: uuid.New(), State: "running",
		Kind: domain.JobAutoDeleteOrphanMedicalCertificate, Version: 1, Payload: json.RawMessage(`{}`)}
}

// A truncated batch means a later chunk holds a timestamp psycopg2 cannot
// convert: the returned rows are processed and the run completes normally.
func TestOrphanMedicalProcessesCompleteChunksBeforeTruncation(t *testing.T) {
	first, second := "00000000-0000-4000-8000-000000000401", "00000000-0000-4000-8000-000000000402"
	store := &orphanMedicalTestStore{batch: domain.OrphanMedicalBatch{
		Certificates: []domain.OrphanMedicalCertificate{orphanMedicalTestCertificate(first), orphanMedicalTestCertificate(second)},
		Truncated:    true,
	}}
	objects := &orphanMedicalTestObjects{location: "private"}
	cleanup := NewOrphanMedicalCleanup(store, objects, time.Now,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	if err := cleanup.Run(context.Background(), orphanMedicalTestJob()); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(store.documents) != 2 || store.documents[0] != first || store.documents[1] != second {
		t.Fatalf("documents %v", store.documents)
	}
	if len(store.audited) != 2 {
		t.Fatalf("audited %v", store.audited)
	}
	if !store.completed {
		t.Fatal("job not completed")
	}
}

// document.delete() raising ends the run after the storage file is deleted:
// earlier certificates stay deleted, later ones are untouched, and the task
// returns normally.
func TestOrphanMedicalStopsAtDocumentCollectorFailure(t *testing.T) {
	first, second, third := "00000000-0000-4000-8000-000000000501", "00000000-0000-4000-8000-000000000502",
		"00000000-0000-4000-8000-000000000503"
	store := &orphanMedicalTestStore{
		batch: domain.OrphanMedicalBatch{Certificates: []domain.OrphanMedicalCertificate{
			orphanMedicalTestCertificate(first), orphanMedicalTestCertificate(second), orphanMedicalTestCertificate(third),
		}},
		documentErr: map[string]error{second: errors.New("protected reference")},
	}
	objects := &orphanMedicalTestObjects{location: "private"}
	cleanup := NewOrphanMedicalCleanup(store, objects, time.Now,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	if err := cleanup.Run(context.Background(), orphanMedicalTestJob()); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(store.documents) != 2 || store.documents[0] != first || store.documents[1] != second {
		t.Fatalf("document deletes %v", store.documents)
	}
	if len(objects.deleted) != 2 {
		t.Fatalf("storage deletes %v", objects.deleted)
	}
	if len(store.audited) != 1 || store.audited[0] != first {
		t.Fatalf("audited %v", store.audited)
	}
	if !store.completed {
		t.Fatal("job not completed")
	}
}
