package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"path"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// DataRestoreObjects is the S3 default storage the restore views use.
type DataRestoreObjects interface {
	Location() string
	Open(ctx context.Context, key string) (*documentobjects.Object, error)
	Delete(ctx context.Context, key string) error
	List(ctx context.Context, prefix, delimiter string) ([]string, []string, error)
}

// DataRestoreProgressReader is progress.snapshot's cache read.
type DataRestoreProgressReader interface {
	Snapshot(ctx context.Context, id string) (repository.DataRestoreProgressValue, error)
}

// DataRestoreSettings are the core.settings values the views read.
type DataRestoreSettings struct {
	SecretKey      string
	Version        string // RUNNING_VERSION
	LockConfigured bool   // DATA_RESTORE_LOCK_PATH != ''
	MaxUploadBytes int64  // DATA_RESTORE_MAX_UPLOAD_BYTES
}

// DataRestore mirrors BE/instance/restore/views.py: the receipts, the
// operation lock, require_single, the actions and their storage cleanup, the
// signed recovery downloads and the restore_instance_data publish. Archive
// validation of uploads and the restore itself stay with Django.
type DataRestore struct {
	store    *repository.DataRestores
	progress DataRestoreProgressReader
	objects  DataRestoreObjects
	locks    repository.RestoreOperationLocker
	tasks    celery.Publisher
	settings DataRestoreSettings
	now      func() time.Time
	// archives enables upload validation (nil: an admissible upload is refused).
	archives *DataRestoreArchives
}

func NewDataRestore(store *repository.DataRestores, progress DataRestoreProgressReader, objects DataRestoreObjects,
	locks repository.RestoreOperationLocker, tasks celery.Publisher, settings DataRestoreSettings, now func() time.Time) *DataRestore {
	return &DataRestore{store: store, progress: progress, objects: objects, locks: locks, tasks: tasks, settings: settings, now: now}
}

// DataRestoreReply is one view answer: a JSON body, the missing-relations
// attachment (Raw) or a storage stream.
type DataRestoreReply struct {
	Status      int
	Body        any
	Raw         []byte
	Disposition string
	Stream      *DataRestoreStream
	// ReferrerPolicy is the download view's no-referrer header.
	ReferrerPolicy string
}

// DataRestoreStream is PrintingService._stream_file_response's object.
type DataRestoreStream struct {
	Object          *documentobjects.Object
	ContentType     string
	Disposition     string
	ContentLength   *int64
	ContentEncoding string
}

const (
	dataRestoreSingleText    = "Il ripristino richiede una istanza self-hosted con una sola associazione."
	dataRestoreLockMissing   = "Aggiorna la distribuzione self-hosted per abilitare il coordinamento del ripristino."
	dataRestorePendingText   = "Un ripristino è già in corso."
	dataRestoreUploadText    = "Carica un file ZIP entro il limite indicato."
	dataRestoreNoBackup      = "Backup di sicurezza non disponibile."
	dataRestoreDownloadSalt  = "instance.data-restore.recovery-download"
	dataRestoreDownloadAge   = 60 * 60
	dataRestoreTask          = "instance.restore.execute"
	dataRestoreTimeLimit     = 3600
	dataRestoreSoftTimeLimit = 3300
)

func dataRestoreError(status int, text string) *DataRestoreReply {
	return &DataRestoreReply{Status: status, Body: domain.RequestObject{{Name: "error", Value: text}}}
}

// dataRestoreBusy maps operation_lock's Busy exceptions; other lock failures
// are unhandled exceptions.
func dataRestoreBusy(err error) (*DataRestoreReply, error) {
	for _, busy := range []error{domain.ErrRestoreBusy, domain.ErrRestoreVolumeUnavailable, domain.ErrRestoreLockRequired} {
		if errors.Is(err, busy) {
			return dataRestoreError(409, busy.Error()), nil
		}
	}
	return nil, err
}

// requireSingle is replacement.require_single: false raises RestoreError.
func dataRestoreSingle(ctx context.Context, config *domain.DataRestoreInstance, others func(context.Context, string) (bool, error)) (bool, error) {
	if config == nil || !config.SelfHosted || config.SupportMultipleAssociations || config.PrimaryAssociationID == nil {
		return false, nil
	}
	exists, err := others(ctx, *config.PrimaryAssociationID)
	return !exists, err
}

// ---------------------------------------------------------------------------
// public_operation
// ---------------------------------------------------------------------------

func (s *DataRestore) public(ctx context.Context, op *domain.DataRestoreReceipt) (domain.RequestObject, error) {
	parsed, err := dto.ParseProfileTablesJSON(op.Preview)
	if err != nil {
		return nil, err
	}
	preview, ok := parsed.(domain.RequestObject)
	if !ok {
		return nil, errors.New("preview is not a dict") // op.preview.items(): AttributeError
	}
	filtered := domain.RequestObject{}
	for _, field := range preview {
		if field.Name != "missing_relation_details" && field.Name != "tutor_reassignment_details" {
			filtered = append(filtered, field)
		}
	}
	progress, err := s.snapshot(ctx, op)
	if err != nil {
		return nil, err
	}
	var token any
	if op.BackupPath != "" {
		token = s.downloadToken(op)
	}
	return domain.RequestObject{
		{Name: "id", Value: op.ID}, {Name: "state", Value: op.State}, {Name: "stage", Value: op.Stage},
		{Name: "preview", Value: filtered}, {Name: "error", Value: op.Error},
		{Name: "created_at", Value: dto.DRFUTCDateTime(op.CreatedAt)}, {Name: "updated_at", Value: dto.DRFUTCDateTime(op.UpdatedAt)},
		{Name: "has_recovery_backup", Value: op.BackupPath != ""}, {Name: "attempts", Value: json.Number(strconv.FormatInt(op.Attempts, 10))},
		{Name: "progress", Value: progress}, {Name: "download_token", Value: token},
	}, nil
}

// snapshot is progress.snapshot: the cached payload of an active receipt
// whose 'attempt' equals op.attempts; every exception is None.
func (s *DataRestore) snapshot(ctx context.Context, op *domain.DataRestoreReceipt) (any, error) {
	if !op.Active() {
		return nil, nil
	}
	cached, err := s.progress.Snapshot(ctx, op.ID)
	if err != nil {
		return nil, domain.ErrDataRestoreNotPorted
	}
	dict, ok := cached.Value.(*repository.PickledDict)
	if !cached.Found || !ok || len(dict.Keys) == 0 {
		// None, falsy values, and value['attempt'] on a non-dict (TypeError).
		return nil, nil
	}
	attempt, ok := dict.Values["attempt"]
	if !ok || !dataRestorePythonEqualsInt(attempt, op.Attempts) {
		return nil, nil
	}
	return dataRestoreRendered(dict)
}

// dataRestorePythonEqualsInt is value == attempts for the decoded types.
func dataRestorePythonEqualsInt(value any, attempts int64) bool {
	switch v := value.(type) {
	case int64:
		return v == attempts
	case bool:
		return (v && attempts == 1) || (!v && attempts == 0)
	case float64:
		return v == float64(attempts) && math.Abs(float64(attempts)) < 1<<53
	}
	return false
}

// dataRestoreRendered converts an unpickled value for DRF's JSONRenderer
// (allow_nan=False: a non-finite float is a 500).
func dataRestoreRendered(value any) (any, error) {
	switch v := value.(type) {
	case *repository.PickledDict:
		out := make(domain.RequestObject, 0, len(v.Keys))
		for _, key := range v.Keys {
			item, err := dataRestoreRendered(v.Values[key])
			if err != nil {
				return nil, err
			}
			out = append(out, domain.RequestField{Name: key, Value: item})
		}
		return out, nil
	case []any:
		out := make([]any, 0, len(v))
		for _, element := range v {
			item, err := dataRestoreRendered(element)
			if err != nil {
				return nil, err
			}
			out = append(out, item)
		}
		return out, nil
	case int64:
		return json.Number(strconv.FormatInt(v, 10)), nil
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, errors.New("Out of range float values are not JSON compliant")
		}
		return v, nil
	case string, bool, nil:
		return v, nil
	}
	return nil, domain.ErrDataRestoreNotPorted
}

// downloadToken is signing.dumps({'operation', 'owner', 'sha256'}, salt=SALT).
func (s *DataRestore) downloadToken(op *domain.DataRestoreReceipt) string {
	payload := dto.PythonJSONDumpsCompact(domain.RequestObject{{Name: "operation", Value: op.ID}, {Name: "owner", Value: op.OwnerID},
		{Name: "sha256", Value: op.BackupSHA256}})
	value := base64.RawURLEncoding.EncodeToString(payload) + ":" + base62(s.now().Unix())
	key := sha256.Sum256([]byte(dataRestoreDownloadSalt + "signer" + s.settings.SecretKey))
	mac := hmac.New(sha256.New, key[:])
	mac.Write([]byte(value))
	return value + ":" + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

// validDownloadToken is valid_download_token: BadSignature (and expiry) is
// False; other loads exceptions escape.
func (s *DataRestore) validDownloadToken(token string, present bool, op *domain.DataRestoreReceipt) (bool, error) {
	if !present {
		return false, nil
	}
	signed, err := djangoSigningLoads(token, dataRestoreDownloadSalt, s.settings.SecretKey, dataRestoreDownloadAge, s.now())
	if err != nil || !signed.valid {
		return false, err
	}
	payload, ok := signed.payload.(map[string]any)
	if !ok || len(payload) != 3 {
		return false, nil
	}
	expected := map[string]string{"operation": op.ID, "owner": op.OwnerID, "sha256": op.BackupSHA256}
	for key, want := range expected {
		if text, ok := payload[key].(string); !ok || text != want {
			return false, nil
		}
	}
	return true, nil
}

func (s *DataRestore) publics(ctx context.Context, receipts []domain.DataRestoreReceipt) ([]any, error) {
	out := make([]any, 0, len(receipts))
	for i := range receipts {
		value, err := s.public(ctx, &receipts[i])
		if err != nil {
			return nil, err
		}
		out = append(out, value)
	}
	return out, nil
}

// recoveryPage is recovery_page(owner, before).
func (s *DataRestore) recoveryPage(ctx context.Context, owner, before string) (domain.RequestObject, *DataRestoreReply, error) {
	var cursor *domain.DataRestoreReceipt
	if before != "" {
		parsed, err := paymentInfoParseUUID(before)
		if err != nil {
			return nil, dataRestoreError(400, "Pagina dei backup non valida."), nil
		}
		cursor, err = s.store.Owned(ctx, parsed.String(), owner)
		if err != nil {
			return nil, nil, err
		}
		if cursor == nil {
			return nil, nil, domain.ErrDataRestoreNotFound
		}
	}
	rows, err := s.store.RecoveryPage(ctx, owner, cursor)
	if err != nil {
		return nil, nil, err
	}
	page := rows
	if len(page) > 10 {
		page = page[:10]
	}
	backups, err := s.publics(ctx, page)
	if err != nil {
		return nil, nil, err
	}
	var next any
	if len(rows) > 10 {
		next = rows[9].ID
	}
	return domain.RequestObject{{Name: "backups", Value: backups}, {Name: "backups_next", Value: next}}, nil, nil
}

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

// Overview is DataRestoreView.get.
func (s *DataRestore) Overview(ctx context.Context, user string) (*DataRestoreReply, error) {
	config, err := s.store.Instance(ctx)
	if err != nil {
		return nil, err
	}
	reason := ""
	single, err := dataRestoreSingle(ctx, config, s.store.OtherAssociations)
	if err != nil {
		return nil, err
	}
	if !single {
		reason = dataRestoreSingleText
	} else if !s.settings.LockConfigured {
		reason = dataRestoreLockMissing
	}
	history, err := s.store.History(ctx, user)
	if err != nil {
		return nil, err
	}
	active, err := s.store.Active(ctx, user)
	if err != nil {
		return nil, err
	}
	var activeBody any
	if active != nil {
		if activeBody, err = s.public(ctx, active); err != nil {
			return nil, err
		}
	}
	historyBody, err := s.publics(ctx, history)
	if err != nil {
		return nil, err
	}
	page, _, err := s.recoveryPage(ctx, user, "")
	if err != nil {
		return nil, err
	}
	body := domain.RequestObject{{Name: "available", Value: reason == ""}, {Name: "reason", Value: reason},
		{Name: "max_upload_bytes", Value: json.Number(strconv.FormatInt(s.settings.MaxUploadBytes, 10))},
		{Name: "active", Value: activeBody}, {Name: "history", Value: historyBody}}
	return &DataRestoreReply{Status: 200, Body: append(body, page...)}, nil
}

// Backups is DataRestoreBackupsView.get (before: query_params.get('before')).
func (s *DataRestore) Backups(ctx context.Context, user, before string) (*DataRestoreReply, error) {
	page, reply, err := s.recoveryPage(ctx, user, before)
	if reply != nil || err != nil {
		return reply, err
	}
	return &DataRestoreReply{Status: 200, Body: page}, nil
}

// DataRestoreUpload is request.FILES.get('file') after the upload handlers:
// the bytes of the 'file' upload completed within the limit (nil: none).
type DataRestoreUpload func(limit int64) ([]byte, error)

// Upload is DataRestoreView.post for request.user (user).
func (s *DataRestore) Upload(ctx context.Context, user string, upload DataRestoreUpload) (*DataRestoreReply, error) {
	lease, err := s.locks.Acquire(ctx, false, true)
	if err != nil {
		return dataRestoreBusy(err)
	}
	defer lease.Close()
	pending, err := s.store.Pending(ctx)
	if err != nil {
		return nil, err
	}
	if pending {
		return dataRestoreError(409, dataRestorePendingText), nil
	}
	config, err := s.store.Instance(ctx)
	if err != nil {
		return nil, err
	}
	single, err := dataRestoreSingle(ctx, config, s.store.OtherAssociations)
	if err != nil {
		return nil, err
	}
	if !single {
		return dataRestoreError(400, dataRestoreSingleText), nil
	}
	content, err := upload(s.settings.MaxUploadBytes)
	if err != nil {
		return nil, err
	}
	if content == nil {
		return dataRestoreError(400, dataRestoreUploadText), nil
	}
	if s.archives == nil {
		return nil, domain.ErrDataRestoreNotPorted
	}
	return s.acceptUpload(ctx, user, config, content)
}

// Read is DataRestoreActionView.get.
func (s *DataRestore) Read(ctx context.Context, user, id, action string) (*DataRestoreReply, error) {
	op, err := s.store.Owned(ctx, id, user)
	if err != nil {
		return nil, err
	}
	if op == nil {
		return nil, domain.ErrDataRestoreNotFound
	}
	switch {
	case action == "missing-relations":
		parsed, err := dto.ParseProfileTablesJSON(op.Preview)
		if err != nil {
			return nil, err
		}
		preview, ok := parsed.(domain.RequestObject)
		if !ok {
			return nil, errors.New("preview is not a dict")
		}
		detail := func(name string) any {
			if value, ok := preview.Lookup(name); ok {
				return value
			}
			return []any{}
		}
		raw := dto.PythonJSONDumpsIndent(domain.RequestObject{{Name: "operation_id", Value: op.ID}, {Name: "source_sha256", Value: op.SHA256},
			{Name: "missing_relations", Value: detail("missing_relation_details")},
			{Name: "tutor_reassignments", Value: detail("tutor_reassignment_details")}}, 2)
		return &DataRestoreReply{Status: 200, Raw: raw, Disposition: `attachment; filename="rapporto-backup-` + op.ID + `.json"`}, nil
	case action == "status":
		body, err := s.public(ctx, op)
		if err != nil {
			return nil, err
		}
		return &DataRestoreReply{Status: 200, Body: body}, nil
	case action != "backup" || op.BackupPath == "":
		return dataRestoreError(404, dataRestoreNoBackup), nil
	}
	return s.streamBackup(ctx, op)
}

// streamBackup is stream_backup on the S3 storage.
func (s *DataRestore) streamBackup(ctx context.Context, op *domain.DataRestoreReceipt) (*DataRestoreReply, error) {
	filename := "backup-prima-del-ripristino-" + op.ID + ".zip"
	key, err := documentkey.S3Key(op.BackupPath, s.objects.Location())
	if err != nil {
		return nil, domain.ErrDataRestoreNotPorted
	}
	object, err := s.objects.Open(ctx, key)
	if err != nil {
		return nil, err
	}
	contentType, _, _, err := pymime.GuessType(filename)
	if err != nil {
		_ = object.Body.Close()
		return nil, err
	}
	if contentType == "" {
		contentType = "application/octet-stream"
		if object.ContentType != nil && *object.ContentType != "" {
			contentType = *object.ContentType
		}
	}
	disposition, _ := contentDisposition(true, filename)
	stream := &DataRestoreStream{Object: object, ContentType: contentType, Disposition: *disposition, ContentLength: object.ContentLength}
	if object.ContentEncoding != nil {
		stream.ContentEncoding = *object.ContentEncoding
	}
	return &DataRestoreReply{Status: 200, Stream: stream}, nil
}

// Download is DataRestoreDownloadView.get (no authentication).
func (s *DataRestore) Download(ctx context.Context, id, token string, present bool) (*DataRestoreReply, error) {
	op, err := s.store.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if op == nil {
		return nil, domain.ErrDataRestoreNotFound
	}
	valid, err := s.validDownloadToken(token, present, op)
	if err != nil {
		return nil, err
	}
	if !valid {
		return dataRestoreError(403, "Link di download non valido o scaduto."), nil
	}
	config, err := s.store.Instance(ctx)
	if err != nil {
		return nil, err
	}
	owner, err := s.store.Account(ctx, op.OwnerID)
	if err != nil {
		return nil, err
	}
	if owner == nil || !dataRestoreAdministrator(owner, config) || config.PrimaryAssociationID == nil ||
		*config.PrimaryAssociationID != op.AssociationID {
		return dataRestoreError(403, "Download non autorizzato."), nil
	}
	if op.BackupPath == "" {
		return dataRestoreError(404, dataRestoreNoBackup), nil
	}
	reply, err := s.streamBackup(ctx, op)
	if reply != nil {
		reply.ReferrerPolicy = "no-referrer"
	}
	return reply, err
}

// dataRestoreAdministrator is is_instance_administrator(user, config).
func dataRestoreAdministrator(user *domain.Account, config *domain.DataRestoreInstance) bool {
	if !user.Active || user.Deleted || config == nil || !config.SelfHosted {
		return false
	}
	return user.Superuser || (config.PrimaryAssociationID != nil && config.PrimaryOwnerID != nil && *config.PrimaryOwnerID == user.ID)
}

// DataRestoreData is request.data, parsed on first access.
type DataRestoreData func() (any, error)

// dataRestoreGet is request.data.get(name): AttributeError (a 500) for a
// body that is not a dict.
func dataRestoreGet(data DataRestoreData, name string) (any, error) {
	value, err := data()
	if err != nil {
		return nil, err
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, errors.New("request.data has no get")
	}
	found, _ := object.Lookup(name)
	return found, nil
}

func dataRestoreIsTrue(value any) bool {
	flag, ok := value.(bool)
	return ok && flag
}

// Act is DataRestoreActionView.post.
func (s *DataRestore) Act(ctx context.Context, user, id, action string, data DataRestoreData) (*DataRestoreReply, error) {
	lease, err := s.locks.Acquire(ctx, true, true)
	if err != nil {
		return dataRestoreBusy(err)
	}
	defer lease.Close()
	tx, err := s.store.Begin(ctx)
	if err != nil {
		return nil, err
	}
	committed := false
	defer func() {
		if !committed {
			_ = tx.Rollback()
		}
	}()
	op, reply, err := s.admitAction(ctx, tx, user, id, action, data)
	if reply != nil || err != nil {
		if reply != nil && reply.Status < 400 {
			// A Response returned inside transaction.atomic(): its writes commit.
			if err = tx.Commit(); err != nil {
				return nil, err
			}
			committed = true
		}
		return reply, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	committed = true
	if action == "dismiss" {
		return s.reply(ctx, 200, op)
	}
	if op.State == "cancelled" {
		s.cleanup(ctx, op)
		return s.reply(ctx, 202, op)
	}
	publishContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
	defer cancel()
	limit, soft := int64(dataRestoreTimeLimit), int64(dataRestoreSoftTimeLimit)
	if _, err = s.tasks.Publish(publishContext, celery.Task{Name: dataRestoreTask, Args: []any{op.ID}, ArgsList: true, ID: op.ID,
		TimeLimit: &limit, SoftTimeLimit: &soft}); err != nil {
		body, err := s.public(ctx, op)
		if err != nil {
			return nil, err
		}
		return &DataRestoreReply{Status: 503, Body: domain.RequestObject{
			{Name: "error", Value: "Invio al worker non confermato. Usa Riprendi per riprovare."}, {Name: "operation", Value: body}}}, nil
	}
	return s.reply(ctx, 202, op)
}

func (s *DataRestore) reply(ctx context.Context, status int, op *domain.DataRestoreReceipt) (*DataRestoreReply, error) {
	body, err := s.public(ctx, op)
	if err != nil {
		return nil, err
	}
	return &DataRestoreReply{Status: status, Body: body}, nil
}

// admitAction is the transaction.atomic() block: a reply ends the request
// (a 2xx one commits), otherwise op is the receipt to finish with.
func (s *DataRestore) admitAction(ctx context.Context, tx *repository.DataRestoreTx, user, id, action string,
	data DataRestoreData) (*domain.DataRestoreReceipt, *DataRestoreReply, error) {
	op, err := tx.Lock(ctx, id, user)
	if err != nil {
		return nil, nil, err
	}
	if op == nil {
		return nil, nil, domain.ErrDataRestoreNotFound
	}
	config, err := tx.Instance(ctx)
	if err != nil {
		return nil, nil, err
	}
	single, err := dataRestoreSingle(ctx, config, tx.OtherAssociations)
	if err != nil {
		return nil, nil, err
	}
	if !single {
		return nil, dataRestoreError(400, dataRestoreSingleText), nil
	}
	save := func(changes repository.DataRestoreChanges) error {
		now := s.now().UTC().Truncate(time.Microsecond)
		if err := tx.Save(ctx, op.ID, changes, now); err != nil {
			return err
		}
		dataRestoreApply(op, changes, now)
		return nil
	}
	switch action {
	case "delete-backup":
		if op.Active() {
			return nil, dataRestoreError(400, "Attendi il termine del ripristino prima di eliminare il backup di sicurezza."), nil
		}
		confirm, err := dataRestoreGet(data, "confirm")
		if err != nil {
			return nil, nil, err
		}
		if !dataRestoreIsTrue(confirm) {
			return nil, dataRestoreError(400, "Conferma l’eliminazione del backup di sicurezza."), nil
		}
		if op.BackupPath != "" {
			if err := s.deleteObject(ctx, op.BackupPath); err != nil {
				return nil, dataRestoreError(503, "Eliminazione del backup non riuscita. Riprova."), nil
			}
			if err := save(repository.DataRestoreChanges{{Column: "backup_path", Value: ""}, {Column: "backup_sha256", Value: ""}}); err != nil {
				return nil, nil, err
			}
		}
		reply, err := s.reply(ctx, 200, op)
		return nil, reply, err
	case "dismiss":
		switch op.State {
		case "failed", "completed", "cancelled", "expired":
		default:
			return nil, dataRestoreError(400, "Puoi chiudere solo un ripristino terminato."), nil
		}
		preview, err := dataRestorePreview(op)
		if err != nil {
			return nil, nil, err
		}
		dismissed := false
		for i, field := range preview {
			if field.Name == "dismissed" {
				preview[i].Value, dismissed = true, true
			}
		}
		if !dismissed {
			preview = append(preview, domain.RequestField{Name: "dismissed", Value: true})
		}
		if err := save(repository.DataRestoreChanges{{Column: "preview", Value: string(dto.PythonJSONDumps(preview))}}); err != nil {
			return nil, nil, err
		}
	case "cancel":
		if op.State != "review" {
			return nil, dataRestoreError(400, "Puoi annullare solo un backup in attesa di conferma."), nil
		}
		if err := save(repository.DataRestoreChanges{{Column: "state", Value: "cancelled"}, {Column: "stage", Value: "cancelled"}}); err != nil {
			return nil, nil, err
		}
	case "start", "resume":
		if op.State == "completed" {
			reply, err := s.reply(ctx, 200, op)
			return nil, reply, err
		}
		if op.Active() {
			// The exclusive operation lock proves no worker runs: redeliver.
			return op, nil, nil
		}
		if op.State != "review" || action == "resume" {
			return nil, dataRestoreError(400, "Carica e valida nuovamente il backup."), nil
		}
		confirmation, err := dataRestoreGet(data, "confirmation")
		if err != nil {
			return nil, nil, err
		}
		if text, ok := confirmation.(string); !ok || text != "RIPRISTINA" {
			return nil, dataRestoreError(400, "Scrivi RIPRISTINA per confermare la sostituzione."), nil
		}
		if op.CreatedAt.Before(s.now().Add(-24 * time.Hour)) {
			return nil, dataRestoreError(400, "La validazione è scaduta. Carica nuovamente il backup."), nil
		}
		if op.Version != s.settings.Version || op.AssociationID != *config.PrimaryAssociationID {
			return nil, dataRestoreError(400, "L’istanza è cambiata. Carica nuovamente il backup."), nil
		}
		preview, err := dataRestorePreview(op)
		if err != nil {
			return nil, nil, err
		}
		missing, _ := preview.Lookup("missing_media")
		if dto.PythonTruthy(missing) {
			allow, err := dataRestoreGet(data, "allow_missing_media")
			if err != nil {
				return nil, nil, err
			}
			if !dataRestoreIsTrue(allow) {
				return nil, dataRestoreError(400, "Conferma l’assenza degli allegati indicati."), nil
			}
		}
		pending, err := tx.Pending(ctx)
		if err != nil {
			return nil, nil, err
		}
		if pending {
			return nil, dataRestoreError(409, dataRestorePendingText), nil
		}
		allow, err := dataRestoreGet(data, "allow_missing_media")
		if err != nil {
			return nil, nil, err
		}
		if err := save(repository.DataRestoreChanges{{Column: "state", Value: "queued"}, {Column: "stage", Value: "queued"},
			{Column: "allow_missing_media", Value: dataRestoreIsTrue(allow)}}); err != nil {
			return nil, nil, err
		}
	default:
		return nil, dataRestoreError(404, "Azione non disponibile."), nil
	}
	return op, nil, nil
}

func dataRestorePreview(op *domain.DataRestoreReceipt) (domain.RequestObject, error) {
	parsed, err := dto.ParseProfileTablesJSON(op.Preview)
	if err != nil {
		return nil, err
	}
	preview, ok := parsed.(domain.RequestObject)
	if !ok {
		return nil, errors.New("preview is not a dict")
	}
	return preview, nil
}

// dataRestoreApply is save_operation's setattr on the in-memory receipt.
func dataRestoreApply(op *domain.DataRestoreReceipt, changes repository.DataRestoreChanges, now time.Time) {
	for _, change := range changes {
		switch change.Column {
		case "state":
			op.State = change.Value.(string)
		case "stage":
			op.Stage = change.Value.(string)
		case "upload_path":
			op.UploadPath = change.Value.(string)
		case "backup_path":
			op.BackupPath = change.Value.(string)
		case "backup_sha256":
			op.BackupSHA256 = change.Value.(string)
		case "allow_missing_media":
			op.AllowMissingMedia = change.Value.(bool)
		case "preview":
			op.Preview = json.RawMessage(change.Value.(string))
		case "media":
			op.Media = json.RawMessage(change.Value.(string))
		}
	}
	op.UpdatedAt = now
}

// deleteObject is default_storage.delete(name).
func (s *DataRestore) deleteObject(ctx context.Context, name string) error {
	return dataRestoreDeleteObject(ctx, s.objects, name)
}

// cleanup is execution.cleanup: the upload object and the operation's
// temporary prefixes; any failure ends it (the task retries later).
func (s *DataRestore) cleanup(ctx context.Context, op *domain.DataRestoreReceipt) {
	save := func(changes repository.DataRestoreChanges) error {
		now := s.now().UTC().Truncate(time.Microsecond)
		if err := s.store.Save(ctx, op.ID, changes, now); err != nil {
			return err
		}
		dataRestoreApply(op, changes, now)
		return nil
	}
	if op.UploadPath != "" {
		if s.deleteObject(ctx, op.UploadPath) != nil || save(repository.DataRestoreChanges{{Column: "upload_path", Value: ""}}) != nil {
			return
		}
	}
	if s.cleanPrefix(ctx, "restores/"+op.ID+"/upload") != nil {
		return
	}
	if op.State != "completed" {
		if s.cleanPrefix(ctx, "restores/"+op.ID+"/media") != nil {
			return
		}
		_ = save(repository.DataRestoreChanges{{Column: "media", Value: "{}"}})
	}
}

// cleanPrefix is execution.clean_prefix over S3Storage.listdir.
func (s *DataRestore) cleanPrefix(ctx context.Context, prefix string) error {
	return dataRestoreCleanPrefix(ctx, s.objects, prefix)
}

// DataRestoreStorage is the part of the S3 default storage that
// execution.cleanup, clean_prefix and prune_storage use.
type DataRestoreStorage interface {
	Location() string
	Delete(ctx context.Context, key string) error
	List(ctx context.Context, prefix, delimiter string) ([]string, []string, error)
}

// dataRestoreDeleteObject is default_storage.delete(name).
func dataRestoreDeleteObject(ctx context.Context, objects DataRestoreStorage, name string) error {
	key, err := documentkey.S3Key(name, objects.Location())
	if err != nil {
		return err
	}
	return objects.Delete(ctx, key)
}

// dataRestoreListdir is S3Storage.listdir(prefix): the directory and file
// names relative to the prefix.
func dataRestoreListdir(ctx context.Context, objects DataRestoreStorage, prefix string) ([]string, []string, error) {
	listed, err := documentkey.S3Key(prefix, objects.Location())
	if err != nil {
		return nil, nil, err
	}
	if listed != "" && !strings.HasSuffix(listed, "/") {
		listed += "/"
	}
	directories, keys, err := objects.List(ctx, listed, "/")
	if err != nil {
		return nil, nil, err
	}
	relative := func(name string) (string, error) {
		base := path.Clean(listed)
		cleaned := path.Clean(name)
		if !strings.HasPrefix(cleaned, base+"/") {
			return "", errors.New("listed name outside the prefix")
		}
		return cleaned[len(base)+1:], nil
	}
	var dirs, files []string
	for _, key := range keys {
		if key == listed {
			continue
		}
		name, err := relative(key)
		if err != nil {
			return nil, nil, err
		}
		files = append(files, name)
	}
	for _, directory := range directories {
		name, err := relative(directory)
		if err != nil {
			return nil, nil, err
		}
		dirs = append(dirs, name)
	}
	return dirs, files, nil
}

// dataRestoreCleanPrefix is execution.clean_prefix over S3Storage.listdir.
func dataRestoreCleanPrefix(ctx context.Context, objects DataRestoreStorage, prefix string) error {
	directories, files, err := dataRestoreListdir(ctx, objects, prefix)
	if err != nil {
		return err
	}
	for _, name := range files {
		if err := dataRestoreDeleteObject(ctx, objects, prefix+"/"+name); err != nil {
			return err
		}
	}
	for _, name := range directories {
		if err := dataRestoreCleanPrefix(ctx, objects, prefix+"/"+name); err != nil {
			return err
		}
	}
	return nil
}
