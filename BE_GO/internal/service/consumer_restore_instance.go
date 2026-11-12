package service

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/jmoiron/sqlx"
)

// ConsumerRestoreInstanceTask is instance.tasks.restore_instance_data's Celery name.
const ConsumerRestoreInstanceTask = "instance.restore.execute"

const (
	restoreGeneric       = "Ripristino non riuscito. I dati precedenti sono conservati. Verifica il backup e riprova."
	restoreProgressTTL   = 86400 * time.Second
	restoreBusyCountdown = 10
)

// ConsumerRestoreObjects is the S3 default storage the restore reads the
// upload from and stages, stores and prunes objects in.
type ConsumerRestoreObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
	Delete(ctx context.Context, key string) error
	List(ctx context.Context, prefix, delimiter string) ([]string, []string, error)
	Download(ctx context.Context, key string, destination io.Writer) error
	Missing(err error) bool
}

// ConsumerRestoreLocks is operation_lock over DATA_RESTORE_LOCK_PATH.
type ConsumerRestoreLocks interface {
	Acquire(ctx context.Context, exclusive, required bool) (io.Closer, error)
}

// ConsumerRestoreCacheStore is the Django cache the progress snapshot and
// cache.clear() use.
type ConsumerRestoreCacheStore interface {
	Set(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) error
	Clear(ctx context.Context) error
}

// RestoreLimits are DATA_RESTORE_MAX_EXPANDED_BYTES, _JSON_BYTES and _FILE_BYTES.
type RestoreLimits = restoreLimits

// ConsumerRestoreSettings are the settings restore_instance_data reads.
type ConsumerRestoreSettings struct {
	TempDir string
	// Version is settings.RUNNING_VERSION.
	Version string
	Limits  RestoreLimits
}

// ConsumerRestoreInstance runs instance.restore.execute
// (restore_instance_data(operation_id) → execution.execute) on the prefork
// consumer: acks_late, reject_on_worker_lost, time limits 3600/3300 s,
// max_retries=None, no CoordinatedTask admission (instance.restore.*).
//
// Plan, in the prefork child before the job is accepted, takes the
// exclusive operation lock, drains the Go transactions, reads the receipt
// and the instance, downloads and validates the upload, builds the recovery
// export and validates it, computes the deletion plan, and runs the whole
// database part of replace() (the Collector delete, import_all in
// replacement mode through internal/importeffects, restore_user_links) in
// one open transaction. Anything the port does not reproduce, and any read
// or database error there, rolls back and hands the message to Django
// before any effect. Run performs Django's effects in its order (receipt
// saves, progress, recovery upload, attachment staging), then the receipt's
// completion inside the transaction and the commit, cache.clear(), the
// cleanup and the pruning; a RestoreError (or any Python error) at its
// point becomes the failed receipt Django writes.
type ConsumerRestoreInstance struct {
	store    *repository.ConsumerRestoreInstance
	db       *sqlx.DB
	locks    ConsumerRestoreLocks
	export   *ConsumerExportAssociation
	imports  *ConsumerImportAssociation
	deletion *repository.RestoreDeletion
	cache    ConsumerRestoreCacheStore
	groups   ConsumerExportGroups
	objects  ConsumerRestoreObjects
	cleanup  *DataRestoreCleanup
	settings ConsumerRestoreSettings
	now      func() time.Time
	ids      func() string
	logger   *slog.Logger
	recovery *associationexport.Plan
}

func NewConsumerRestoreInstance(db *sqlx.DB, locks ConsumerRestoreLocks, export *ConsumerExportAssociation,
	imports *ConsumerImportAssociation, cache ConsumerRestoreCacheStore, groups ConsumerExportGroups, objects ConsumerRestoreObjects,
	recoveryBackups *string, settings ConsumerRestoreSettings, now func() time.Time, ids func() string,
	logger *slog.Logger) (*ConsumerRestoreInstance, error) {
	deletion, err := repository.NewRestoreDeletion()
	if err != nil {
		return nil, err
	}
	cleanup := NewDataRestoreCleanup(repository.NewConsumerRestoreCleanups(db), objects, nil, recoveryBackups, now, logger)
	return &ConsumerRestoreInstance{store: repository.NewConsumerRestoreInstance(db), db: db, locks: locks, export: export,
		imports: imports, deletion: deletion, cache: cache, groups: groups, objects: objects, cleanup: cleanup, settings: settings,
		now: now, ids: ids, logger: logger, recovery: export.plan.Recovery()}, nil
}

func (s *ConsumerRestoreInstance) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: -1, Plan: s.plan, Run: s.run, Cleanup: s.release, Prefork: true, AcksLate: true,
		RejectOnWorkerLost: true, TimeLimit: "3600", SoftTimeLimit: "3300"}
}

// restoreStaged is one stage_media upload.
type restoreStaged struct {
	identity, member, name, key, contentType, encoding string
	size                                               int64
}

// restoreCall is what Plan prepared for Run.
type restoreCall struct {
	lease    io.Closer
	busy     string
	missing  bool
	terminal bool
	inactive bool
	op       *domain.DataRestoreReceipt

	// The first RestoreError (or other Python error) Django meets, by the
	// point it is raised at.
	precheck, validation, recoveryFailure, deletionFailure, replaceFailure string

	uploadTemp   string
	export       *consumerRestoreExportService
	recoveryName string
	recoveryKey  string
	recoverySHA  string
	media        []restoreStaged
	totalBytes   int64
	tx           *sqlx.Tx
	importEvents []restoreProgressEvent
}

func (s *ConsumerRestoreInstance) release(request *celeryworker.Request) {
	call, _ := request.State.(*restoreCall)
	if call == nil {
		return
	}
	if call.tx != nil {
		_ = call.tx.Rollback()
		call.tx = nil
	}
	if call.export != nil {
		call.export.remove()
	}
	if call.uploadTemp != "" {
		_ = os.Remove(call.uploadTemp)
	}
	if call.lease != nil {
		_ = call.lease.Close()
		call.lease = nil
	}
}

var restoreOperationID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// plan is execute() up to the replacement's commit, from reads and one
// uncommitted transaction.
func (s *ConsumerRestoreInstance) plan(ctx context.Context, request *celeryworker.Request) error {
	values, err := consumerPrintBind([]string{"operation_id"}, 1, request)
	if err != nil {
		return err
	}
	id, ok := values["operation_id"].(string)
	if !ok || !restoreOperationID.MatchString(id) {
		return celeryworker.ErrHandoff
	}
	call := &restoreCall{}
	request.State = call
	lease, err := s.locks.Acquire(ctx, true, true)
	switch {
	case errors.Is(err, domain.ErrRestoreLockRequired):
		call.busy = "Il volume di coordinamento del ripristino non è configurato. Aggiorna la distribuzione self-hosted."
		return nil
	case errors.Is(err, domain.ErrRestoreVolumeUnavailable):
		call.busy = "Il volume di coordinamento non è disponibile."
		return nil
	case errors.Is(err, domain.ErrRestoreBusy):
		call.busy = "Un’altra operazione sui dati è in corso. Riprova tra poco."
		return nil
	case err != nil:
		return celeryworker.ErrHandoff
	}
	call.lease = lease
	if err := s.store.Barrier(ctx); err != nil {
		if ctx.Err() != nil {
			return celeryworker.ErrHandoff
		}
		_ = lease.Close()
		call.lease = nil
		call.busy = "Il database sta terminando operazioni precedenti. Riprova tra poco."
		return nil
	}
	op, err := s.store.Receipt(ctx, nil, id)
	if err != nil {
		return celeryworker.ErrHandoff
	}
	if op == nil {
		call.missing = true
		return nil
	}
	call.op = op
	if !op.Active() {
		if op.State == "completed" || op.State == "failed" || op.State == "cancelled" {
			call.terminal = true
		} else {
			call.inactive = true
		}
		return nil
	}
	if err := s.prepare(ctx, call, op); err != nil {
		var failure *restoreError
		if errors.As(err, &failure) {
			return nil
		}
		if call.tx != nil {
			_ = call.tx.Rollback()
			call.tx = nil
		}
		return celeryworker.ErrHandoff
	}
	return nil
}

// stop records a RestoreError met at a point and ends the preparation.
func restoreStop(target *string, message string) error {
	*target = message
	return &restoreError{message: message}
}

// prepare reads, validates and prepares every effect of the active receipt.
func (s *ConsumerRestoreInstance) prepare(ctx context.Context, call *restoreCall, op *domain.DataRestoreReceipt) error {
	if op.Attempts >= 3 {
		return restoreStop(&call.precheck, "Ripristino interrotto più volte. I dati precedenti sono conservati; verifica il worker e carica nuovamente il backup.")
	}
	configs, err := s.store.Configurations(ctx)
	if err != nil {
		return err
	}
	if len(configs) != 1 {
		return restoreStop(&call.precheck, restoreGeneric) // DoesNotExist / MultipleObjectsReturned
	}
	config := configs[0]
	single := config.SelfHosted && !config.Multiple && config.Primary.Valid
	if single {
		other, err := s.store.OtherAssociations(ctx, config.Primary.String)
		if err != nil {
			return err
		}
		single = !other
	}
	if !single {
		return restoreStop(&call.precheck, "Il ripristino richiede una istanza self-hosted con una sola associazione.")
	}
	account, err := s.store.Account(ctx, op.OwnerID)
	if err != nil {
		return err
	}
	if account == nil || !config.PrimaryOwner.Valid || !config.PrimaryOwnerExists {
		return errConsumerImportUnsupported
	}
	administrator := account.Active && !account.Deleted && config.SelfHosted &&
		(account.Superuser || config.PrimaryOwner.String == account.ID)
	if config.Primary.String != op.AssociationID || !administrator {
		return restoreStop(&call.precheck, "Il titolare o l’associazione sono cambiati. Valida nuovamente il backup.")
	}
	if op.Version != s.settings.Version {
		return restoreStop(&call.precheck, "La versione dell’applicazione è cambiata. Valida nuovamente il backup.")
	}
	if config.PrimaryDeleted.Valid && config.PrimaryDeleted.Bool {
		return errConsumerImportUnsupported // auditlog's default manager would not see the association
	}
	// local_copy(op.upload_path) and digest.
	if limits := s.settings.Limits; op.UploadPath == "" || limits.Expanded <= 0 || limits.JSON <= 0 || limits.File <= 0 {
		return errConsumerImportUnsupported
	}
	key, err := documentkey.S3Key(op.UploadPath, s.objects.Location())
	if err != nil {
		return errConsumerImportUnsupported
	}
	dir, err := filepath.Abs(s.settings.TempDir)
	if err != nil {
		return err
	}
	temp, err := consumerImportTempName(dir)
	if err != nil {
		return err
	}
	call.uploadTemp = temp.Name()
	err = s.objects.Download(ctx, key, temp)
	if closeErr := temp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		if s.objects.Missing(err) {
			return restoreStop(&call.validation, restoreGeneric)
		}
		return err
	}
	digest, err := restoreDigest(call.uploadTemp)
	if err != nil {
		return err
	}
	if digest != op.SHA256 {
		return restoreStop(&call.validation, "Il file caricato è cambiato dopo la validazione.")
	}
	run := &consumerImportRun{s: s.imports, ctx: ctx, path: call.uploadTemp}
	archive, err := validateRestoreArchive(run, call.uploadTemp, s.settings.Limits)
	var failure *restoreError
	if errors.As(err, &failure) {
		return restoreStop(&call.validation, failure.message)
	}
	if err != nil {
		return err
	}
	defer archive.Close()
	if archive.missingMedia > 0 && !op.AllowMissingMedia {
		return restoreStop(&call.validation, "Conferma esplicitamente l’assenza degli allegati indicati.")
	}
	// RecoveryExport(op, progress).export(): the archive from reads, validated.
	backup, err := s.prepareRecovery(ctx, call, op)
	if errors.As(err, &failure) {
		return restoreStop(&call.recoveryFailure, failure.message)
	}
	if err != nil {
		return err
	}
	defer backup.Close()
	// deletion_plan(backup) before staging.
	read, err := s.db.BeginTxx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return err
	}
	_, err = s.deletion.Plan(ctx, read, backup.identityList, s.now)
	_ = read.Rollback()
	if message, ok := restoreDeletionMessage(err); ok {
		return restoreStop(&call.deletionFailure, message)
	}
	if err != nil {
		return err
	}
	if err := s.prepareMedia(ctx, call, op, archive); err != nil {
		return err
	}
	if err := s.cleanup.plan(ctx, domain.Job{}); err != nil {
		return err
	}
	return s.prepareReplacement(ctx, call, op, archive, backup, config)
}

// restoreDeletionMessage maps deletion_plan's outcomes: its RestoreErrors and
// a ProtectedError (any other exception: the generic message).
func restoreDeletionMessage(err error) (string, bool) {
	var deletion *repository.RestoreDeletionError
	switch {
	case errors.As(err, &deletion):
		return deletion.Message, true
	case errors.Is(err, repository.ErrRestoreDeletionProtected):
		return restoreGeneric, true
	}
	return "", false
}

func restoreDigest(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

// prepareRecovery is RecoveryExport.export() up to its upload: the export
// (EXCLUDED_FIELDS of User only, recovery_<op>_<uuid>.zip) and
// save_to_storage's validation of it.
func (s *ConsumerRestoreInstance) prepareRecovery(ctx context.Context, call *restoreCall, op *domain.DataRestoreReceipt) (*restoreArchive, error) {
	export := s.export
	row, err := export.store.ExportAssociation(ctx, op.AssociationID)
	if err != nil {
		return nil, err
	}
	if !row.Found {
		return nil, errConsumerImportUnsupported
	}
	exportCall := &consumerRestoreExportCall{association: op.AssociationID, associationValid: true, associationRow: row}
	if exportCall.credentials, exportCall.passwords, err = export.credentialPasswords(ctx, op.AssociationID, row.Owner); err != nil {
		return nil, err
	}
	service := &consumerRestoreExportService{s: export, call: exportCall, association: row, plan: s.recovery,
		archiveName: "recovery_" + op.ID + "_" + s.ids() + ".zip"}
	call.export = service
	if err := service.prepare(ctx); err != nil {
		return nil, err
	}
	run := &consumerImportRun{s: s.imports, ctx: ctx, path: service.zipPath}
	backup, err := validateRestoreArchive(run, service.zipPath, s.settings.Limits)
	if err != nil {
		return nil, err
	}
	if len(backup.missingRelations) > 0 {
		backup.Close()
		return nil, &restoreError{message: "Il backup di sicurezza contiene relazioni mancanti. Nessun dato è stato sostituito."}
	}
	if backup.missingBinary > 0 {
		backup.Close()
		return nil, &restoreError{message: "Il backup di sicurezza non contiene tutti gli allegati attuali. Correggi i file mancanti prima di ripristinare."}
	}
	name, key, _, _, err := s.saveName("restores/" + op.ID + "/recovery/" + s.ids() + ".zip")
	if err != nil {
		backup.Close()
		return nil, err
	}
	call.recoveryName, call.recoveryKey = name, key
	if call.recoverySHA, err = restoreDigest(service.zipPath); err != nil {
		backup.Close()
		return nil, err
	}
	return backup, nil
}

// saveName is default_storage.save(name, ...)'s returned name, key and
// guessed content type; a taken name (get_available_name would pick a
// random alternative) is unsupported.
func (s *ConsumerRestoreInstance) saveName(path string) (string, string, string, string, error) {
	cleaned, err := documentkey.SaveName(path)
	if err != nil {
		return "", "", "", "", errConsumerImportUnsupported
	}
	key, err := documentkey.S3Key(cleaned, s.objects.Location())
	if err != nil {
		return "", "", "", "", errConsumerImportUnsupported
	}
	if !s.imports.settings.Overwrite {
		exists, err := s.objects.Exists(context.Background(), key)
		if err != nil || exists {
			return "", "", "", "", errConsumerImportUnsupported
		}
	}
	kind, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return "", "", "", "", errConsumerImportUnsupported
	}
	if !known {
		kind = "application/octet-stream"
	}
	return cleaned, key, kind, encoding, nil
}

// prepareMedia chooses stage_media's names: restores/<op>/media/<uuid4>/<basename>.
func (s *ConsumerRestoreInstance) prepareMedia(ctx context.Context, call *restoreCall, op *domain.DataRestoreReceipt, archive *restoreArchive) error {
	seen := map[string]bool{}
	for _, identity := range archive.mediaKeys {
		member := archive.media[identity]
		filename := member[strings.LastIndex(member, "/")+1:]
		name, key, kind, encoding, err := s.saveName("restores/" + op.ID + "/media/" + s.ids() + "/" + filename)
		if err != nil {
			return err
		}
		if seen[key] {
			return errConsumerImportUnsupported
		}
		seen[key] = true
		entry := archive.zip.byName[member]
		call.media = append(call.media, restoreStaged{identity: identity, member: member, name: name, key: key, contentType: kind,
			encoding: encoding, size: int64(entry.UncompressedSize64)})
		call.totalBytes += int64(entry.UncompressedSize64)
	}
	return nil
}

// prepareReplacement is replace()'s transaction up to complete(): the
// deletion plan's delete(), import_all() in replacement mode and
// restore_user_links(), left open for Run.
func (s *ConsumerRestoreInstance) prepareReplacement(ctx context.Context, call *restoreCall, op *domain.DataRestoreReceipt, archive,
	backup *restoreArchive, config repository.RestoreConfiguration) error {
	staged := map[string]string{}
	for _, item := range call.media {
		staged[item.identity] = item.name
	}
	replacement := &restoreReplacement{target: config.Primary.String, owner: config.PrimaryOwner.String, missing: map[string]map[string]bool{},
		media: staged, processed: map[string]bool{}, store: s.store}
	for _, relation := range archive.missingRelations {
		key := relation.model + "\x00" + relation.record
		if replacement.missing[key] == nil {
			replacement.missing[key] = map[string]bool{}
		}
		replacement.missing[key][relation.field] = true
	}
	for _, rows := range archive.records {
		replacement.total += len(rows)
	}
	replacement.replacedUsers = map[string]bool{}
	for _, id := range backup.identityList["User"] {
		replacement.replacedUsers[id] = true
	}
	run := &consumerImportRun{s: s.imports, ctx: ctx, path: call.uploadTemp, replace: replacement, stats: celery.Dict{}}
	opened, err := consumerImportOpenArchive(call.uploadTemp)
	if err != nil {
		return err
	}
	run.archive = opened
	defer opened.Close()
	// Records the port does not reproduce are handed off before the
	// transaction consumes sequence values.
	if err := run.preflight(); err != nil {
		return err
	}
	tx, err := s.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	call.tx = tx
	replacement.tx = tx
	collector, err := s.deletion.Plan(ctx, tx, backup.identityList, s.now)
	if message, ok := restoreDeletionMessage(err); ok {
		return s.replaceFailed(call, message)
	}
	if err != nil {
		return err
	}
	if err := collector.Delete(ctx); err != nil {
		return err
	}
	run.effects = &importeffects.Effects{Plan: s.imports.plan, Tx: tx, Audit: s.imports.audit, Now: s.now, NewUUID: s.ids}
	err = run.importAll()
	if err == nil {
		err = replacement.restoreUserLinks(run, backup.identityList["User"], archive.records["User"])
	}
	if err == nil && (len(run.warnings) > 0 || restoreStat(run.stats, "deferred_fks_failed") || restoreStat(run.stats, "m2m_failed")) {
		err = &restoreError{message: "Non è stato possibile ricostruire tutte le relazioni. I dati precedenti sono stati conservati."}
	}
	var python *consumerImportPyError
	var failure *restoreError
	switch {
	case errors.As(err, &failure):
		return s.replaceFailed(call, failure.message)
	case errors.As(err, &python):
		return s.replaceFailed(call, restoreGeneric)
	case err != nil:
		return err
	}
	call.importEvents = replacement.events
	return nil
}

func (s *ConsumerRestoreInstance) replaceFailed(call *restoreCall, message string) error {
	if call.tx != nil {
		_ = call.tx.Rollback()
		call.tx = nil
	}
	call.replaceFailure = message
	return nil
}

func restoreStat(stats celery.Dict, name string) bool {
	for _, item := range stats {
		if item.Key == name {
			switch v := item.Value.(type) {
			case int64:
				return v != 0
			case int:
				return v != 0
			}
			return true
		}
	}
	return false
}

// run is execute() from the 'running' receipt on, and the task's Busy
// deferral (defer_for_maintenance(countdown=10)).
func (s *ConsumerRestoreInstance) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, _ := request.State.(*restoreCall)
	if call == nil {
		return nil, errors.New("consumer restore: no plan")
	}
	if call.busy != "" {
		return nil, &celeryworker.Retry{Exc: &celeryworker.Exception{Type: "Busy", Module: "instance.restore.locking",
			Args: []any{call.busy}}, Countdown: restoreBusyCountdown, Maintenance: true}
	}
	if call.missing {
		return nil, &celeryworker.Exception{Type: "DataRestore.DoesNotExist", Module: "instance.models",
			Args: []any{"DataRestore matching query does not exist."}}
	}
	op := call.op
	if call.terminal {
		s.cleanupReceipt(ctx, op)
		return nil, nil
	}
	if call.inactive {
		return nil, nil
	}
	progress := &restoreProgress{s: s, op: op}
	message, committed, err := s.execute(ctx, call, op, progress)
	if err != nil && !committed {
		s.logger.Error("data restore failed", "code", "data_restore_failed")
		message = restoreGeneric
	}
	if message != "" {
		current, err := s.store.Receipt(ctx, nil, op.ID)
		if err != nil || current == nil {
			return nil, errors.New("consumer restore: receipt refresh failed")
		}
		*op = *current
		if op.State != "completed" {
			changes := []repository.RestoreChange{{Column: "state", Value: "failed"}, {Column: "stage", Value: "failed"},
				{Column: "error", Value: message}}
			if err := s.save(ctx, nil, op, changes); err != nil {
				return nil, err
			}
			progress.publish(ctx, restoreProgressEvent{phase: "failed", force: true})
		}
	}
	current, err := s.store.Receipt(ctx, nil, op.ID)
	if err != nil || current == nil {
		return nil, errors.New("consumer restore: receipt refresh failed")
	}
	*op = *current
	if !op.Active() {
		s.cleanupReceipt(ctx, op)
	}
	if op.State == "completed" {
		if err := s.cleanup.prune(ctx, domain.Job{}); err != nil {
			s.logger.Warn("restore storage pruning will be retried", "code", "data_restore_prune")
		}
	}
	return nil, nil
}

// execute performs the effects; it returns the failure message Django
// records, whether the replacement committed, or an unexpected error.
func (s *ConsumerRestoreInstance) execute(ctx context.Context, call *restoreCall, op *domain.DataRestoreReceipt,
	progress *restoreProgress) (string, bool, error) {
	if call.precheck != "" {
		return call.precheck, false, nil
	}
	if err := s.save(ctx, nil, op, []repository.RestoreChange{{Column: "state", Value: "running"}, {Column: "stage", Value: "backup"},
		{Column: "attempts", Value: op.Attempts + 1}}); err != nil {
		return "", false, err
	}
	progress.publish(ctx, restoreProgressEvent{phase: "validating", force: true})
	if call.validation != "" {
		return call.validation, false, nil
	}
	progress.publish(ctx, restoreProgressEvent{phase: "backup"})
	if call.export != nil {
		for _, event := range call.export.events {
			percent := float64(event.percent)
			progress.publish(ctx, restoreProgressEvent{phase: "backup", percent: &percent, detail: event.label, hasDetail: true})
		}
	}
	if call.recoveryFailure != "" {
		return call.recoveryFailure, false, nil
	}
	body, err := os.ReadFile(call.export.zipPath)
	if err != nil {
		return "", false, err
	}
	if err := s.objects.Put(ctx, call.recoveryKey, body, "application/zip", ""); err != nil {
		return "", false, err
	}
	if err := s.save(ctx, nil, op, []repository.RestoreChange{{Column: "backup_path", Value: call.recoveryName},
		{Column: "backup_sha256", Value: call.recoverySHA}}); err != nil {
		return "", false, err
	}
	call.export.remove()
	if call.deletionFailure != "" {
		return call.deletionFailure, false, nil
	}
	if err := s.save(ctx, nil, op, []repository.RestoreChange{{Column: "stage", Value: "files"}}); err != nil {
		return "", false, err
	}
	progress.publish(ctx, restoreProgressEvent{phase: "files"})
	if err := dataRestoreCleanPrefix(ctx, s.objects, "restores/"+op.ID+"/media"); err != nil {
		return "", false, err
	}
	archive, err := consumerImportOpenArchive(call.uploadTemp)
	if err != nil {
		return "", false, err
	}
	media := map[string]string{}
	var copied int64
	for index, item := range call.media {
		data, err := archive.read(item.member)
		if err == nil {
			err = s.objects.Put(ctx, item.key, data, item.contentType, item.encoding)
		}
		if err != nil {
			archive.Close()
			return "", false, err
		}
		media[item.identity] = item.name
		copied += item.size
		completed, total := float64(copied), float64(call.totalBytes)
		progress.publish(ctx, restoreProgressEvent{phase: "files", completed: &completed, total: &total, unit: "bytes",
			detail: restoreItoa(index+1) + " / " + restoreItoa(len(call.media)) + " allegati", hasDetail: true,
			force: index+1 == len(call.media)})
	}
	archive.Close()
	encoded, err := json.Marshal(media)
	if err != nil {
		return "", false, err
	}
	if err := s.save(ctx, nil, op, []repository.RestoreChange{{Column: "media", Value: string(encoded)}}); err != nil {
		return "", false, err
	}
	if err := s.save(ctx, nil, op, []repository.RestoreChange{{Column: "stage", Value: "restoring"}}); err != nil {
		return "", false, err
	}
	progress.publish(ctx, restoreProgressEvent{phase: "restoring"})
	for _, event := range call.importEvents {
		progress.publish(ctx, event)
	}
	if call.replaceFailure != "" {
		return call.replaceFailure, false, nil
	}
	tx := call.tx
	if tx == nil {
		return "", false, errors.New("consumer restore: no replacement transaction")
	}
	call.tx = nil
	completedAt := s.now().UTC().Truncate(time.Microsecond)
	if err := s.save(ctx, tx, op, []repository.RestoreChange{{Column: "state", Value: "completed"}, {Column: "stage", Value: "completed"},
		{Column: "error", Value: ""}, {Column: "completed_at", Value: completedAt}}); err != nil {
		_ = tx.Rollback()
		return "", false, err
	}
	if err := tx.Commit(); err != nil {
		return "", false, err
	}
	_ = s.cache.Clear(ctx)
	percent := 100.0
	progress.publish(ctx, restoreProgressEvent{phase: "completed", percent: &percent, force: true})
	return "", true, nil
}

func restoreItoa(n int) string { return strconv.Itoa(n) }

// save is save_operation(op, **changes) on q (nil: autocommit).
func (s *ConsumerRestoreInstance) save(ctx context.Context, q sqlx.ExecerContext, op *domain.DataRestoreReceipt, changes []repository.RestoreChange) error {
	now := s.now().UTC().Truncate(time.Microsecond)
	if err := s.store.Save(ctx, q, op.ID, changes, now); err != nil {
		return err
	}
	for _, change := range changes {
		switch change.Column {
		case "state":
			op.State = change.Value.(string)
		case "stage":
			op.Stage = change.Value.(string)
		case "attempts":
			op.Attempts = change.Value.(int64)
		case "backup_path":
			op.BackupPath = change.Value.(string)
		case "backup_sha256":
			op.BackupSHA256 = change.Value.(string)
		case "error":
			op.Error = change.Value.(string)
		case "media":
			op.Media = json.RawMessage(change.Value.(string))
		}
	}
	op.UpdatedAt = now
	return nil
}

// cleanupReceipt is execution.cleanup(op); every failure is logged.
func (s *ConsumerRestoreInstance) cleanupReceipt(ctx context.Context, op *domain.DataRestoreReceipt) {
	if err := s.cleanup.cleanup(ctx, domain.Job{}, op); err != nil {
		s.logger.Warn("restore temporary-file cleanup will be retried", "code", "restore_cleanup")
	}
}

// restoreProgressEvent is one RestoreProgress.publish call.
type restoreProgressEvent struct {
	phase            string
	completed, total *float64
	unit, detail     string
	hasDetail, force bool
	percent          *float64
}

// restoreProgress is RestoreProgress: a 1 s throttle within a phase, the
// cache snapshot and the WebSocket event.
type restoreProgress struct {
	s        *ConsumerRestoreInstance
	op       *domain.DataRestoreReceipt
	mu       sync.Mutex
	phase    string
	started  time.Time
	lastSent time.Time
	sequence int64
}

func (p *restoreProgress) publish(ctx context.Context, event restoreProgressEvent) {
	p.mu.Lock()
	defer p.mu.Unlock()
	now := time.Now()
	changed := event.phase != p.phase
	if changed {
		p.phase, p.started = event.phase, now
	}
	if !changed && !event.force && !p.lastSent.IsZero() && now.Sub(p.lastSent) < time.Second {
		return
	}
	elapsed := math.Max(0, now.Sub(p.started).Seconds())
	percent := event.percent
	var eta any
	if event.total != nil && *event.total != 0 && event.completed != nil {
		value := math.Min(100, math.Max(0, *event.completed*100 / *event.total))
		percent = &value
		if elapsed >= 5 && *event.completed > 0 && *event.completed < *event.total {
			eta = int64(math.RoundToEven(elapsed * (*event.total - *event.completed) / *event.completed))
		}
	}
	p.sequence++
	optional := func(value *float64) any {
		if value == nil {
			return nil
		}
		return int64(*value)
	}
	var unit, detail any
	if event.unit != "" {
		unit = event.unit
	}
	if event.hasDetail {
		detail = event.detail
	}
	var rounded any
	if percent != nil {
		rounded = int64(math.RoundToEven(*percent))
	}
	payload := associationexport.Dict{{Key: "operation_id", Value: p.op.ID}, {Key: "attempt", Value: p.op.Attempts},
		{Key: "sequence", Value: p.sequence}, {Key: "phase", Value: event.phase},
		{Key: "updated_at", Value: associationexport.PythonISOFormat(p.s.now())}, {Key: "completed", Value: optional(event.completed)},
		{Key: "total", Value: optional(event.total)}, {Key: "unit", Value: unit}, {Key: "detail", Value: detail},
		{Key: "percent", Value: rounded}, {Key: "elapsed_seconds", Value: int64(math.RoundToEven(elapsed))}, {Key: "eta_seconds", Value: eta}}
	p.lastSent = now
	_ = p.s.cache.Set(ctx, "data-restore-progress:"+p.op.ID, payload, restoreProgressTTL)
	if p.s.groups != nil {
		_ = p.s.groups.GroupSend(ctx, "notifications_user_"+p.op.OwnerID, channels.Dict{{Key: "type", Value: "restore_progress"},
			{Key: "payload", Value: toChannelsValue(payload)}})
	}
}

// restoreReplacement is ReplacementImporter's state and overrides.
type restoreReplacement struct {
	target, owner string
	// replacedUsers are the accounts of the replaced association (the
	// safety backup's User keys).
	replacedUsers map[string]bool
	missing       map[string]map[string]bool
	media         map[string]string
	processed     map[string]bool
	total         int
	events        []restoreProgressEvent
	store         *repository.ConsumerRestoreInstance
	tx            *sqlx.Tx
}

func (rep *restoreReplacement) publish(event restoreProgressEvent) {
	rep.events = append(rep.events, event)
}

// registered is _register_imported_object's progress report.
func (rep *restoreReplacement) registered(model, key string) {
	rep.processed[model+"\x00"+key] = true
	completed, total := float64(len(rep.processed)), float64(rep.total)
	rep.publish(restoreProgressEvent{phase: "restoring", completed: &completed, total: &total, unit: "records", detail: model,
		hasDetail: true})
}

// normalizeOptionalLinks is _normalize_optional_links.
func (rep *restoreReplacement) normalizeOptionalLinks(model *importeffects.Model, data domain.RequestObject) (domain.RequestObject, error) {
	record, err := importStr(importGetValue(data, model.PK.Name))
	if err != nil {
		return nil, err
	}
	fields := rep.missing[model.Name+"\x00"+record]
	if len(fields) == 0 {
		return data, nil
	}
	data = append(domain.RequestObject(nil), data...)
	for _, field := range model.Fields {
		if fields[field.Attname] {
			data = importSet(data, field.Attname, nil)
		}
	}
	return data, nil
}

// createOwnerUser is ReplacementImporter._create_owner_user: the local owner
// stands for the archive's.
func (rep *restoreReplacement) createOwnerUser(r *consumerImportRun, old string) error {
	owner := &importeffects.Instance{Model: r.s.plan.Model("User"), Values: map[string]importeffects.Value{
		"user_id": importeffects.UUIDValue(rep.owner)}}
	r.ownerUser = owner
	r.uuidMapping[r.ownerUserID] = rep.owner
	r.reusedUserPKs[r.ownerUserID] = true
	r.register("User", r.ownerUserID, owner)
	r.importedModels["User"] = true
	return nil
}

// importSportAssociation is ReplacementImporter._import_sport_association:
// a fresh instance of the archived record with the local key, owner and
// deleted flag, saved with force_update=True.
func (rep *restoreReplacement) importSportAssociation(r *consumerImportRun, data domain.RequestObject, old string) error {
	model := r.s.plan.Model("SportAssociation")
	data, err := rep.normalizeOptionalLinks(model, data)
	if err != nil {
		return err
	}
	values := append(domain.RequestObject(nil), data...)
	values = importSet(values, "sport_association_id", rep.target)
	values = importSet(values, "user_id", rep.owner)
	values = importSet(values, "deleted", importeffects.BoolValue(false))
	association, err := r.buildInstance(model, values)
	if err != nil {
		return err
	}
	if err := r.effects.SaveForceUpdate(r.ctx, association); err != nil {
		return err
	}
	r.association = association
	r.uuidMapping[r.associationID] = rep.target
	r.register("SportAssociation", r.associationID, association)
	r.importedModels["SportAssociation"] = true
	return nil
}

// importFiles is ReplacementImporter._import_files: every imported document
// takes its staged key (” without one) and every imported subscription its
// staged signature key and public URL.
func (rep *restoreReplacement) importFiles(r *consumerImportRun) error {
	for _, old := range r.objectOrder["Document"] {
		document := r.documentMapping[old]
		document.Values["filepath"] = importeffects.StrValue(rep.media["Document:"+old])
		if err := r.effects.SaveFields(r.ctx, document, "filepath"); err != nil {
			return err
		}
	}
	for _, old := range r.objectOrder["Subscription"] {
		subscription := r.importedObjects["Subscription"][old]
		key := rep.media["Subscription:"+old]
		subscription.Values["signature_storage_key"] = importeffects.StrValue(key)
		if key != "" {
			subscription.Values["signature_url"] = r.signaturePublicURL(key)
		}
		if err := r.effects.SaveFields(r.ctx, subscription, "signature_storage_key", "signature_url"); err != nil {
			return err
		}
	}
	r.setStat("files_imported", int64(len(rep.media)))
	return nil
}

// restoreUserLinks is ReplacementImporter.restore_user_links(old_user_ids).
func (rep *restoreReplacement) restoreUserLinks(r *consumerImportRun, oldUsers []string, rows []domain.RequestObject) error {
	ctx, tx := r.ctx, rep.tx
	incoming := map[string]bool{}
	for _, row := range rows {
		id, err := importStr(importGetValue(row, "user_id"))
		if err != nil {
			return err
		}
		incoming[id] = true
	}
	old := map[string]bool{}
	keys := []string{}
	for _, id := range oldUsers {
		old[id] = true
		if !incoming[id] {
			keys = append(keys, id)
		}
	}
	removed, err := rep.store.DetachedUsers(ctx, tx, keys, rep.owner)
	if err != nil {
		return err
	}
	for _, id := range removed {
		if err := rep.store.Detach(ctx, tx, id, rep.owner, rep.target); err != nil {
			return err
		}
	}
	if err := rep.store.DeactivateUsers(ctx, tx, removed); err != nil {
		return err
	}
	userModel := r.s.plan.Model("User")
	text := func(value any) (any, error) {
		switch v := value.(type) {
		case nil:
			return nil, nil
		case string:
			return v, nil
		case importeffects.Value:
			if v.IsNone() {
				return nil, nil
			}
			return v.Text()
		}
		return nil, errConsumerImportUnsupported
	}
	for _, raw := range rows {
		row, err := rep.normalizeOptionalLinks(userModel, raw)
		if err != nil {
			return err
		}
		source, err := importStr(importGetValue(row, "user_id"))
		if err != nil {
			return err
		}
		if source == r.ownerUserID {
			columns, casts, values := []string{}, []string{}, []any{}
			for _, name := range []string{"default_payment_category_id", "default_payment_category_courses_id"} {
				var value any
				if rawValue := importGetValue(row, name); importTruthy(rawValue) {
					old, err := importStr(rawValue)
					if err != nil {
						return err
					}
					if value, err = text(r.resolveFK(old, "PaymentCategory")); err != nil {
						return err
					}
				}
				columns, casts, values = append(columns, name), append(casts, "::uuid"), append(values, value)
			}
			if err := rep.store.UpdateUser(ctx, tx, rep.owner, columns, casts, values); err != nil {
				return err
			}
			continue
		}
		object := r.importedObjects["User"][source]
		if object == nil {
			return importError("KeyError: " + source)
		}
		id := object.PK().S
		var connected any
		var active bool
		if r.reusedUserPKs[source] {
			state, err := rep.store.UserState(ctx, tx, id)
			if err != nil {
				return err
			}
			if state.ConnectedUser.Valid {
				connected = state.ConnectedUser.String
			}
			active = state.IsActive
		} else {
			value := object.Get("connected_user_id")
			if !value.IsNone() {
				connected = value.S
			}
			flag := object.Get("is_active")
			if flag.Kind != importeffects.Bool {
				return errConsumerImportUnsupported
			}
			active = flag.B
		}
		archivedConnected, err := importStr(importGetValue(row, "connected_user_id"))
		if err != nil {
			return err
		}
		recover := false
		if connected == nil && !active && archivedConnected == r.ownerUserID {
			if recover, err = rep.store.Detachment(ctx, tx, id, rep.owner, rep.target); err != nil {
				return err
			}
		}
		if r.reusedUserPKs[source] && !old[source] && !recover {
			continue
		}
		if id == rep.owner {
			return &restoreError{message: "Il titolare locale compare anche come altro utente nel backup."}
		}
		columns, casts, values := []string{}, []string{}, []any{}
		if recover {
			activeValue, present := importGet(row, "is_active")
			if !present {
				activeValue = true
			}
			flag, ok := activeValue.(bool)
			if !ok {
				return errConsumerImportUnsupported
			}
			columns, casts, values = append(columns, "is_active"), append(casts, "::boolean"), append(values, flag)
		}
		for _, field := range userModel.Fields {
			if !field.IsFK() {
				continue
			}
			if _, imported := r.importedObjects[field.TargetModel]; !imported {
				continue
			}
			var value any
			if rawValue := importGetValue(row, field.Attname); importTruthy(rawValue) {
				old, err := importStr(rawValue)
				if err != nil {
					return err
				}
				if value, err = text(r.resolveFK(old, field.TargetModel)); err != nil {
					return err
				}
			}
			columns, casts, values = append(columns, field.Column), append(casts, "::"+field.DBType), append(values, value)
		}
		if err := rep.store.UpdateUser(ctx, tx, id, columns, casts, values); err != nil {
			return err
		}
		if recover {
			if err := rep.store.DeleteDetachment(ctx, tx, id, rep.owner, rep.target); err != nil {
				return err
			}
		}
	}
	return nil
}
