package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// OrphanMedicalCleanup is auto_delete_orphan_medical_certificate: for each
// certificate no subscription references, the stored file is deleted, then
// document.delete() (cascading to the certificate), then
// medical_certificate.delete() again on the deleted instance. The task's
// outer except ends the run at the first exception and returns None.
type OrphanMedicalCleanup struct {
	store   domain.OrphanMedicalStore
	objects domain.ExportObjects
	now     func() time.Time
	logger  *slog.Logger
}

func NewOrphanMedicalCleanup(store domain.OrphanMedicalStore, objects domain.ExportObjects, now func() time.Time, logger *slog.Logger) *OrphanMedicalCleanup {
	return &OrphanMedicalCleanup{store: store, objects: objects, now: now, logger: logger}
}

// orphanMedicalStep is one planned iteration: the object key to delete, or
// stop when Django raises at that certificate (no document, a name outside
// AWS_LOCATION).
type orphanMedicalStep struct {
	certificate domain.OrphanMedicalCertificate
	key         string
	stop        bool
}

func (s *OrphanMedicalCleanup) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" ||
		job.Kind != domain.JobAutoDeleteOrphanMedicalCertificate || job.Version != 1 || len(job.Payload) > 65536 ||
		json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	steps, err := s.plan(ctx, job)
	if err != nil {
		return err
	}
	for _, step := range steps {
		if step.stop {
			s.logger.WarnContext(ctx, "orphan medical cleanup stopped", "code", "orphan_medical_stopped")
			break
		}
		// default_storage.delete(file): an error ends the task.
		if err = s.objects.Delete(ctx, step.key); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			s.logger.WarnContext(ctx, "orphan medical storage delete failed", "code", "orphan_medical_storage_delete")
			break
		}
		changes, err := s.deleteDocument(ctx, job, step.certificate)
		if err != nil {
			return err
		}
		if err = s.auditAgain(ctx, job, step.certificate.MedicalID, changes); err != nil {
			return err
		}
	}
	tx, err := s.store.BeginOrphanMedical(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return tx.Commit()
}

// plan reads the certificates once (the iterator's snapshot) and refuses the
// run before any effect when a document in scope has a reference the
// collector would cascade to, or a name the key helpers do not model.
func (s *OrphanMedicalCleanup) plan(ctx context.Context, job domain.Job) ([]orphanMedicalStep, error) {
	tx, err := s.store.BeginOrphanMedical(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	certificates, err := tx.OrphanMedicalCertificates(ctx)
	if err != nil {
		return nil, err
	}
	steps := make([]orphanMedicalStep, 0, len(certificates))
	documents := []string{}
	for _, certificate := range certificates {
		step := orphanMedicalStep{certificate: certificate}
		if certificate.DocumentID == nil || certificate.Created == nil || certificate.Filename == nil {
			// document.creation_date on None raises AttributeError.
			step.stop = true
			steps = append(steps, step)
			continue
		}
		documents = append(documents, *certificate.DocumentID)
		id, err := uuid.Parse(*certificate.DocumentID)
		if err != nil {
			return nil, err
		}
		name, err := documentkey.StoredName(*certificate.Created, id, *certificate.Filename, nil)
		if err != nil {
			return nil, errors.Join(domain.ErrOrphanMedicalUnsupported, err)
		}
		step.key, err = documentkey.S3Key(name, s.objects.Location())
		var outside *documentkey.OutsideLocationError
		if errors.As(err, &outside) {
			// django-storages raises SuspiciousOperation.
			step.stop = true
		} else if err != nil {
			return nil, errors.Join(domain.ErrOrphanMedicalUnsupported, err)
		}
		steps = append(steps, step)
	}
	conflict, err := tx.OrphanMedicalReferenceConflict(ctx, documents)
	if err != nil {
		return nil, err
	}
	if conflict {
		s.logger.WarnContext(ctx, "orphan medical cleanup refused", "code", "orphan_medical_cascade")
		return nil, domain.ErrOrphanMedicalUnsupported
	}
	return steps, tx.Rollback()
}

func (s *OrphanMedicalCleanup) deleteDocument(ctx context.Context, job domain.Job, certificate domain.OrphanMedicalCertificate) (map[string][2]string, error) {
	tx, err := s.store.BeginOrphanMedical(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	changes, err := tx.DeleteOrphanMedicalDocument(ctx, certificate.MedicalID, *certificate.DocumentID, s.now().UTC())
	if err != nil {
		return nil, err
	}
	return changes, tx.Commit()
}

func (s *OrphanMedicalCleanup) auditAgain(ctx context.Context, job domain.Job, medical string, changes map[string][2]string) error {
	tx, err := s.store.BeginOrphanMedical(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.AuditOrphanMedicalDelete(ctx, medical, changes, s.now().UTC()); err != nil {
		return err
	}
	return tx.Commit()
}
