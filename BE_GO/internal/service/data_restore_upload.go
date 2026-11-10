package service

import (
	"context"
	"errors"
	"os"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// DataRestoreUploads is the default storage's write side for the upload.
type DataRestoreUploads interface {
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
}

// DataRestoreArchives enables the upload's archive validation: the import
// plan (AssociationImportService/AssociationExportService metadata), the
// Archive.validate() limits, AWS_S3_FILE_OVERWRITE, the writable storage and
// the receipt id generator.
type DataRestoreArchives struct {
	Plan          *importeffects.Plan
	Limits        struct{ Expanded, JSON, File int64 }
	FileOverwrite bool
	Uploads       DataRestoreUploads
	NewID         func() string
}

// WithArchives returns the service with upload validation enabled.
func (s *DataRestore) WithArchives(archives DataRestoreArchives) *DataRestore {
	copied := *s
	copied.archives = &archives
	return &copied
}

const dataRestoreUploadFailed = "Caricamento non riuscito. Riprova."

// acceptUpload is DataRestoreView.post from Archive(temporary).validate():
// the owner's receipts in review are cancelled and cleaned, the receipt is
// created with the archive's summary, and the upload is stored under
// restores/<id>/upload/archive.zip.
func (s *DataRestore) acceptUpload(ctx context.Context, user string, config *domain.DataRestoreInstance, content []byte) (*DataRestoreReply, error) {
	temp, err := os.CreateTemp("", "data-restore-*.zip")
	if err != nil {
		return nil, err
	}
	path := temp.Name()
	defer os.Remove(path)
	_, err = temp.Write(content)
	if closeErr := temp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, err
	}
	limits := restoreLimits{Expanded: s.archives.Limits.Expanded, JSON: s.archives.Limits.JSON, File: s.archives.Limits.File}
	run := &consumerImportRun{s: &ConsumerImportAssociation{plan: s.archives.Plan}, ctx: ctx, path: path}
	archive, err := validateRestoreArchive(run, path, limits)
	var failure *restoreError
	if errors.As(err, &failure) {
		return dataRestoreError(400, failure.message), nil
	}
	if err != nil {
		return nil, domain.ErrDataRestoreNotPorted
	}
	defer archive.Close()
	preview, err := archive.summary(run)
	if err != nil {
		return nil, domain.ErrDataRestoreNotPorted
	}
	// Keep a bounded number of review uploads per owner.
	reviews, err := s.store.Reviews(ctx, user)
	if err != nil {
		return nil, err
	}
	for i := range reviews {
		previous := &reviews[i]
		changes := repository.DataRestoreChanges{{Column: "state", Value: "cancelled"}, {Column: "stage", Value: "cancelled"}}
		now := s.now().UTC().Truncate(time.Microsecond)
		if err := s.store.Save(ctx, previous.ID, changes, now); err != nil {
			return nil, err
		}
		dataRestoreApply(previous, changes, now)
		s.cleanup(ctx, previous)
	}
	digest, err := restoreDigest(path)
	if err != nil {
		return nil, err
	}
	op, err := s.store.Create(ctx, s.archives.NewID(), user, *config.PrimaryAssociationID, digest, s.settings.Version,
		string(dto.PythonJSONDumps(preview)), s.now().UTC().Truncate(time.Microsecond))
	if err != nil {
		return nil, err
	}
	name := "restores/" + op.ID + "/upload/archive.zip"
	if err := s.storeUpload(ctx, name, content); err != nil {
		changes := repository.DataRestoreChanges{{Column: "state", Value: "failed"}, {Column: "stage", Value: "failed"},
			{Column: "error", Value: dataRestoreUploadFailed}}
		now := s.now().UTC().Truncate(time.Microsecond)
		if saveErr := s.store.Save(ctx, op.ID, changes, now); saveErr != nil {
			return nil, saveErr
		}
		dataRestoreApply(op, changes, now)
		op.Error = dataRestoreUploadFailed
		s.cleanup(ctx, op)
		return dataRestoreError(503, dataRestoreUploadFailed), nil
	}
	changes := repository.DataRestoreChanges{{Column: "upload_path", Value: name}}
	now := s.now().UTC().Truncate(time.Microsecond)
	if err := s.store.Save(ctx, op.ID, changes, now); err != nil {
		return nil, err
	}
	dataRestoreApply(op, changes, now)
	return s.reply(ctx, 201, op)
}

// storeUpload is default_storage.save(name, File(temporary)): the content
// type guessed from the name (no upload content type on a File).
func (s *DataRestore) storeUpload(ctx context.Context, name string, content []byte) error {
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return err
	}
	if !s.archives.FileOverwrite {
		exists, err := s.archives.Uploads.Exists(ctx, key)
		if err != nil {
			return err
		}
		if exists {
			return errors.New("restore upload name exists") // get_available_name would pick another name
		}
	}
	kind, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return err
	}
	if !known {
		kind = "application/octet-stream"
	}
	return s.archives.Uploads.Put(ctx, key, content, kind, encoding)
}

// summary is Archive.summary() for the archives the port validates: legacy
// invoices and tutor reassignments are refused during validation, so their
// counts are zero.
func (a *restoreArchive) summary(run *consumerImportRun) (domain.RequestObject, error) {
	denomination := any("")
	if associations := a.records["SportAssociation"]; len(associations) == 1 {
		if value, ok := importGet(associations[0], "denomination"); ok {
			denomination = value
		}
	}
	exportDate, _ := importGet(a.manifest, "export_date")
	skipped, err := run.skippedRecords(a.manifest)
	if err != nil {
		return nil, err
	}
	skippedObject := domain.RequestObject{}
	for _, item := range skipped {
		count, ok := item.Value.(int64)
		if !ok {
			return nil, errConsumerImportUnsupported
		}
		skippedObject = append(skippedObject, domain.RequestField{Name: item.Key, Value: int64ToNumber(int(count))})
	}
	records := 0
	for name, rows := range a.records {
		if name != "BillingPlan" && name != "PreviewAndCustomFeatures" {
			records += len(rows)
		}
	}
	details := make([]any, 0, len(a.missingRelations))
	for _, relation := range a.missingRelations {
		details = append(details, domain.RequestObject{{Name: "model", Value: relation.model}, {Name: "record_id", Value: relation.record},
			{Name: "field", Value: relation.field}, {Name: "target_model", Value: relation.targetModel},
			{Name: "target_id", Value: relation.target}})
	}
	return domain.RequestObject{{Name: "association", Value: denomination}, {Name: "export_date", Value: exportDate},
		{Name: "skipped_records", Value: skippedObject}, {Name: "records", Value: int64ToNumber(records)},
		{Name: "files", Value: int64ToNumber(len(a.media))}, {Name: "missing_media", Value: int64ToNumber(a.missingMedia)},
		{Name: "legacy_invoices", Value: int64ToNumber(0)}, {Name: "reassigned_tutors", Value: int64ToNumber(0)},
		{Name: "tutor_tax_code_conflicts", Value: int64ToNumber(0)}, {Name: "tutor_reassignment_details", Value: []any{}},
		{Name: "missing_relations", Value: int64ToNumber(len(a.missingRelations))},
		{Name: "missing_relation_details", Value: details}}, nil
}
