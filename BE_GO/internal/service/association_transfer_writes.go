package service

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzipfile"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"golang.org/x/text/encoding/charmap"
)

// AssociationTransferWrites is the state the dispatching export/import paths
// change: the export rows, Django's cache and the validator's lookups.
type AssociationTransferWrites interface {
	ExportFiles(ctx context.Context, association, document string) ([]repository.AssociationExportFile, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	DeleteArchive(ctx context.Context, id, association string, audit domain.LoginAuditContext, at time.Time) error
	DeleteDocument(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error
	SetExportSize(ctx context.Context, id string, size int64, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error
	CacheValue(ctx context.Context, key string) (any, bool, error)
	CacheAdd(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) (bool, error)
	CacheSet(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) error
	CacheDelete(ctx context.Context, keys ...string) error
	ImportConflicts(ctx context.Context, association, owner string, candidates []string) (repository.ImportConflicts, error)
}

// AssociationTransferObjects is the S3 default storage.
type AssociationTransferObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
	Delete(ctx context.Context, key string) error
	Size(ctx context.Context, key string) (int64, error)
}

// AssociationTransferOptions are the dependencies of the dispatching paths.
type AssociationTransferOptions struct {
	Writes        AssociationTransferWrites
	Objects       AssociationTransferObjects
	Tasks         celery.Publisher
	SetupToken    string
	FileOverwrite bool
	NewID         func() string
}

// With returns the service with the dispatching paths enabled.
func (s *AssociationTransfer) With(options AssociationTransferOptions) *AssociationTransfer {
	copied := *s
	copied.options = options
	return &copied
}

// Constants of BE/application/export_progress.py.
const (
	exportActiveTimeout = 2 * time.Hour
	exportTaskTimeout   = 24 * time.Hour
	exportActiveName    = "association-export-active:"
	exportLastName      = "association-export-last:"
	exportTaskName      = "association-export-task:"
	importTaskCacheName = "association-import-task:"
	importTaskTimeout   = 24 * time.Hour // IMPORT_TASK_CACHE_TIMEOUT
)

// setupTokenMatches is constant_time_compare(provided, expected) on the
// header as Django reads it (latin-1 decoded bytes, compared as UTF-8).
func setupTokenMatches(provided, expected string) bool {
	if expected == "" {
		return false
	}
	decoded, err := charmap.ISO8859_1.NewDecoder().String(provided)
	if err != nil {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(decoded), []byte(expected)) == 1
}

// cachedDict is get_active_export/get_last_export: a cached dict or nil.
func (s *AssociationTransfer) cachedDict(ctx context.Context, key string) (*repository.PickledDict, error) {
	value, present, err := s.options.Writes.CacheValue(ctx, key)
	if errors.Is(err, repository.ErrDataRestoreProgressShape) {
		return nil, domain.ErrAssociationTransferUnexpected
	}
	if err != nil || !present {
		return nil, err
	}
	dict, _ := value.(*repository.PickledDict)
	return dict, nil
}

func pickledText(dict *repository.PickledDict, key string) (string, bool) {
	text, ok := dict.Values[key].(string)
	return text, ok
}

// transferJSON renders an unpickled or decoded value as DRF's JSONRenderer
// would; NaN and infinities (rendered as invalid JSON) are refused.
func transferJSON(value any) (any, error) {
	switch v := value.(type) {
	case *repository.PickledDict:
		out := make(map[string]any, len(v.Keys))
		for _, key := range v.Keys {
			item, err := transferJSON(v.Values[key])
			if err != nil {
				return nil, err
			}
			out[key] = item
		}
		return out, nil
	case domain.RequestObject:
		out := make(map[string]any, len(v))
		for _, field := range v {
			item, err := transferJSON(field.Value)
			if err != nil {
				return nil, err
			}
			out[field.Name] = item
		}
		return out, nil
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			converted, err := transferJSON(item)
			if err != nil {
				return nil, err
			}
			out[i] = converted
		}
		return out, nil
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, domain.ErrAssociationTransferUnexpected
		}
		return dto.PythonFloat(v), nil
	case json.Number:
		if number, err := strconv.ParseFloat(string(v), 64); err != nil || math.IsInf(number, 0) {
			if _, ok := new(big.Int).SetString(string(v), 10); !ok {
				return nil, domain.ErrAssociationTransferUnexpected
			}
		}
		return v, nil
	case nil, string, bool, int64:
		return v, nil
	}
	return nil, domain.ErrAssociationTransferUnexpected
}

// taskReady is stored_task(task_id)['status'] in TASK_READY_STATES: the
// stored status alone (no row is not ready).
func (s *AssociationTransfer) taskReady(ctx context.Context, task string) (bool, error) {
	row, err := s.store.TaskResult(ctx, task)
	if err != nil || row == nil {
		return false, err
	}
	return row.Status == "SUCCESS" || row.Status == "FAILURE" || row.Status == "REVOKED", nil
}

// activeExport mirrors active_export after the permission and association checks.
func (s *AssociationTransfer) activeExport(ctx context.Context, association, user string) (*AssociationTransferReply, error) {
	inactive := &AssociationTransferReply{Status: 200, Body: map[string]any{"active": false}}
	withTerminal := func(terminal *repository.PickledDict) (*AssociationTransferReply, error) {
		rendered, err := transferJSON(terminal)
		if err != nil {
			return nil, err
		}
		return &AssociationTransferReply{Status: 200, Body: map[string]any{"active": false, "terminal": rendered}}, nil
	}
	snapshot, err := s.cachedDict(ctx, exportActiveName+association)
	if err != nil {
		return nil, err
	}
	if snapshot == nil {
		terminal, err := s.cachedDict(ctx, exportLastName+association)
		if err != nil {
			return nil, err
		}
		if terminal != nil {
			owner, _ := pickledText(terminal, "user_id")
			status, _ := pickledText(terminal, "status")
			if owner == user && (status == "SUCCESS" || status == "FAILURE") {
				return withTerminal(terminal)
			}
		}
		return inactive, nil
	}
	if owned, _ := pickledText(snapshot, "sport_association_id"); owned != association {
		return inactive, nil
	}
	if owner, _ := pickledText(snapshot, "user_id"); owner != user {
		return inactive, nil
	}
	value, present := snapshot.Values["task_id"]
	if !present {
		return nil, errors.New("active export snapshot without task_id") // KeyError
	}
	task, ok := value.(string)
	if !ok {
		return nil, domain.ErrAssociationTransferUnexpected
	}
	ready, err := s.taskReady(ctx, task)
	if err != nil {
		return nil, err
	}
	if ready {
		// clear_active_export(association, task_id): another task's snapshot stays.
		current, err := s.cachedDict(ctx, exportActiveName+association)
		if err != nil {
			return nil, err
		}
		other := false
		if current != nil {
			currentTask, present := current.Values["task_id"]
			other = !present || currentTask != task
		}
		if !other {
			if err = s.options.Writes.CacheDelete(ctx, exportActiveName+association); err != nil {
				return nil, err
			}
		}
		terminal, err := s.cachedDict(ctx, exportLastName+association)
		if err != nil {
			return nil, err
		}
		if terminal != nil {
			if terminalTask, ok := terminal.Values["task_id"].(string); ok && terminalTask == task {
				return withTerminal(terminal)
			}
		}
		return inactive, nil
	}
	rendered, err := transferJSON(snapshot)
	if err != nil {
		return nil, err
	}
	body := rendered.(map[string]any)
	merged := map[string]any{"active": true}
	for key, item := range body {
		merged[key] = item
	}
	return &AssociationTransferReply{Status: 200, Body: merged}, nil
}

// deleteExport mirrors delete_export from the archive lookup: the stored
// file, then the archive row, then the document row (autocommit each).
func (s *AssociationTransfer) deleteExport(ctx context.Context, association, document string, audit domain.LoginAuditContext) (*AssociationTransferReply, error) {
	rows, err := s.options.Writes.ExportFiles(ctx, association, document)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return transferError(404, exportNotFound), nil
	}
	if len(rows) > 1 {
		return nil, errors.New("export delete matched several archives") // MultipleObjectsReturned
	}
	row := rows[0]
	var key string
	if row.Filepath != nil && *row.Filepath != "" {
		if key, err = documentkey.S3Key(*row.Filepath, s.options.Objects.Location()); err != nil {
			return nil, domain.ErrAssociationTransferUnexpected
		}
	}
	if key != "" {
		if err = s.options.Objects.Delete(ctx, key); err != nil {
			return nil, err
		}
	}
	if err = s.options.Writes.DeleteArchive(ctx, row.ArchiveID, association, audit, s.now()); err != nil {
		return nil, err
	}
	if err = s.options.Writes.DeleteDocument(ctx, row.DocumentID, audit, s.now()); err != nil {
		return nil, err
	}
	return &AssociationTransferReply{Status: 200, Body: map[string]any{"success": true, "message": "Export eliminato"}}, nil
}

// startExport mirrors start_export after the count and before the cache.add.
func (s *AssociationTransfer) startExport(ctx context.Context, association, user string) (*AssociationTransferReply, error) {
	task := s.options.NewID()
	progress := associationexport.Dict{{Key: "percent", Value: 0}, {Key: "phase", Value: "queued"},
		{Key: "label", Value: "Export in attesa"}, {Key: "completed", Value: nil}, {Key: "total", Value: nil}}
	updated := pythonISODateTime(s.now())
	snapshot := associationexport.Dict{{Key: "task_id", Value: task}, {Key: "sport_association_id", Value: association},
		{Key: "user_id", Value: user}, {Key: "status", Value: "PENDING"}, {Key: "ready", Value: false},
		{Key: "estimate", Value: "0%"}, {Key: "progress", Value: progress}, {Key: "updated_at", Value: updated}}
	added, err := s.options.Writes.CacheAdd(ctx, exportActiveName+association, snapshot, exportActiveTimeout)
	if err != nil {
		return nil, err
	}
	if !added {
		return transferError(409, "Un export è già in corso"), nil
	}
	owner := associationexport.Dict{{Key: "sport_association_id", Value: association}, {Key: "user_id", Value: user}}
	rollback := func(cause error) (*AssociationTransferReply, error) {
		_ = s.options.Writes.CacheDelete(context.WithoutCancel(ctx), exportActiveName+association, exportTaskName+task)
		return nil, cause
	}
	if err = s.options.Writes.CacheSet(ctx, exportTaskName+task, owner, exportTaskTimeout); err != nil {
		return rollback(err)
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
	defer cancel()
	if _, err = s.options.Tasks.Publish(publish, celery.Task{Name: "export_association_data", ID: task, Kwargs: celery.Dict{
		{Key: "sport_association_id", Value: association}, {Key: "user_id", Value: user}}}); err != nil {
		return rollback(err)
	}
	return &AssociationTransferReply{Status: 202, Body: map[string]any{"task_id": task, "status": "started", "estimate": "0%",
		"progress":   map[string]any{"percent": 0, "phase": "queued", "label": "Export in attesa", "completed": nil, "total": nil},
		"updated_at": updated, "message": "Export avviato. Riceverai una email quando sarà completato."}}, nil
}

// AssociationImportFile is one uploaded file (request.FILES): the sanitized
// name, the part's content type (main value) and the bytes.
type AssociationImportFile struct {
	Name, ContentType string
	Content           []byte
}

// AssociationImportUpload is request.FILES.get('file') and
// request.data.get('owner_password', ”) of a multipart or form body.
type AssociationImportUpload struct {
	File *AssociationImportFile
	// Password is a string, or *AssociationImportFile when the last value is a file.
	Password any
	// BodySize is the request Content-Length: Django's file upload handlers
	// keep an upload in memory at or under FILE_UPLOAD_MAX_MEMORY_SIZE.
	BodySize int64
}

var fileRequired = &AssociationTransferReply{Status: 400, Body: map[string]string{"error": "File ZIP richiesto"}}

// ImportStart mirrors start_import: default_storage.save of the upload under
// temp/imports/import_<uuid4>.zip, then import_association_data.delay.
func (s *AssociationTransfer) ImportStart(ctx context.Context, upload AssociationImportUpload) (*AssociationTransferReply, error) {
	if upload.File == nil {
		return fileRequired, nil
	}
	password, ok := upload.Password.(string)
	if !ok {
		// start_import passes the UploadedFile to import_association_data.apply_async, whose JSON
		// encoder raises TypeError("Object of type <Class> is not JSON serializable"); the except
		// deletes the saved archive and the cache key, so nothing is stored, cached or published.
		class := "TemporaryUploadedFile"
		if upload.BodySize <= domain.RequestUploadMemory {
			class = "InMemoryUploadedFile"
		}
		return transferError(500, "Errore durante avvio import: Object of type "+class+" is not JSON serializable"), nil
	}
	name := "temp/imports/import_" + s.options.NewID() + ".zip"
	key, err := documentkey.S3Key(name, s.options.Objects.Location())
	if err != nil {
		return nil, domain.ErrAssociationTransferUnexpected
	}
	if !s.options.FileOverwrite {
		exists, err := s.options.Objects.Exists(ctx, key)
		if err != nil {
			return nil, err
		}
		if exists {
			return nil, domain.ErrAssociationTransferUnexpected // get_available_name picks another name
		}
	}
	guessed, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return nil, domain.ErrAssociationTransferUnexpected
	}
	kind := upload.File.ContentType
	if kind == "" {
		kind = guessed
		if !known {
			kind = "application/octet-stream"
		}
	}
	if err = s.options.Objects.Put(ctx, key, upload.File.Content, kind, encoding); err != nil {
		return nil, err
	}
	// The task is registered before dispatch: import/status answers registered imports only.
	id := s.options.NewID()
	rollback := func(cause error) (*AssociationTransferReply, error) {
		// Django deletes the saved archive and the registration and answers its own 500 with the exception text.
		_ = s.options.Objects.Delete(context.WithoutCancel(ctx), key)
		_ = s.options.Writes.CacheDelete(context.WithoutCancel(ctx), importTaskCacheName+id)
		return nil, cause
	}
	if err = s.options.Writes.CacheSet(ctx, importTaskCacheName+id, associationexport.Dict{{Key: "task", Value: importTaskName}},
		importTaskTimeout); err != nil {
		return rollback(err)
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
	defer cancel()
	if _, err = s.options.Tasks.Publish(publish, celery.Task{Name: importTaskName, ID: id, Kwargs: celery.Dict{
		{Key: "zip_file_path", Value: name}, {Key: "owner_password", Value: password}}}); err != nil {
		return rollback(err)
	}
	return &AssociationTransferReply{Status: 202, Body: map[string]any{"task_id": id, "status": "started",
		"message": "Import avviato. Controlla lo stato con task_id."}}, nil
}

const tempNameCharacters = "abcdefghijklmnopqrstuvwxyz0123456789_"

// pythonMkdtemp is tempfile.mkdtemp(): 'tmp' plus eight random characters
// in the temporary directory, mode 0700.
func pythonMkdtemp() (string, error) {
	for attempt := 0; attempt < 100; attempt++ {
		random := make([]byte, 8)
		if _, err := rand.Read(random); err != nil {
			return "", err
		}
		name := []byte("tmp")
		for _, b := range random {
			name = append(name, tempNameCharacters[int(b)%len(tempNameCharacters)])
		}
		path := filepath.Join(os.TempDir(), string(name))
		if err := os.Mkdir(path, 0o700); err == nil {
			return path, nil
		} else if !os.IsExist(err) {
			return "", err
		}
	}
	return "", errors.New("no usable temporary directory name")
}

// pythonOSErrorText is str() of the OSError an open()/write()/close() failure
// raises: '[Errno <errno>] <strerror>: <repr(path)>' for open, the same
// without the path for write and close.
func pythonOSErrorText(err error) (string, bool) {
	var pathErr *os.PathError
	if !errors.As(err, &pathErr) {
		return "", false
	}
	var errno syscall.Errno
	if !errors.As(pathErr.Err, &errno) {
		return "", false
	}
	text := "[Errno " + strconv.Itoa(int(errno)) + "] " + errnoText(errno)
	switch pathErr.Op {
	case "open":
		return text + ": " + dto.PythonStringRepr(pathErr.Path), true
	case "write", "close":
		return text, true
	}
	return "", false
}

// ImportValidate mirrors validate_import: the upload is written to
// <mkdtemp>/<name> and checked by ImportValidator.validate_all().
func (s *AssociationTransfer) ImportValidate(ctx context.Context, upload AssociationImportUpload) (*AssociationTransferReply, error) {
	if upload.File == nil {
		return fileRequired, nil
	}
	dir, err := pythonMkdtemp()
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	path := dir + "/" + upload.File.Name
	if err = os.WriteFile(path, upload.File.Content, 0o644); err != nil {
		if text, ok := pythonOSErrorText(err); ok {
			return &AssociationTransferReply{Status: 500,
				Body: map[string]string{"error": "Errore durante la validazione: " + text}}, nil
		}
		// os.WriteFile reports open/write/close as *os.PathError around an
		// errno: the text above covers every failure a djangoUploadName
		// (printable, no separator, at most 255 characters) can cause.
		return nil, refusal.AppState("import temp write without an errno")
	}
	result, err := s.validateArchive(ctx, path)
	if err != nil {
		var failure *importValidationFailure
		if errors.As(err, &failure) {
			reply := transferError(500, "Errore durante la validazione: "+failure.text)
			if importBodyUnrenderable(reply.Body) {
				return nil, errTransferUnrenderable
			}
			return reply, nil
		}
		return nil, err
	}
	body := result.body()
	// DRF renders with json.dumps and encodes UTF-8: a lone surrogate in the
	// body (from a JSON string in the archive) raises inside the renderer,
	// which is Django's 500 page.
	if importBodyUnrenderable(body) {
		return nil, errTransferUnrenderable
	}
	return &AssociationTransferReply{Status: 200, Body: body}, nil
}

// importBodyUnrenderable reports a lone surrogate anywhere in a response body:
// DRF's JSONRenderer encodes the dumped text as UTF-8, which raises on one.
// The JSON port keeps such a code point in its three-byte (WTF-8) form.
func importBodyUnrenderable(body any) bool {
	switch value := body.(type) {
	case string:
		return importTextHasSurrogate(value)
	case []any:
		for _, item := range value {
			if importBodyUnrenderable(item) {
				return true
			}
		}
	case map[string]any:
		for key, item := range value {
			if importTextHasSurrogate(key) || importBodyUnrenderable(item) {
				return true
			}
		}
	case map[string]string:
		for key, item := range value {
			if importTextHasSurrogate(key) || importTextHasSurrogate(item) {
				return true
			}
		}
	case domain.RequestObject:
		for _, field := range value {
			if importTextHasSurrogate(field.Name) || importBodyUnrenderable(field.Value) {
				return true
			}
		}
	}
	return false
}

func importTextHasSurrogate(text string) bool {
	for _, r := range pystr.Runes(text) {
		if r >= 0xD800 && r <= 0xDFFF {
			return true
		}
	}
	return false
}

// importValidation is ValidationResult.
type importValidation struct {
	valid    bool
	errors   []string
	warnings []string
	info     map[string]any
}

func newImportValidation() *importValidation {
	return &importValidation{valid: true, errors: []string{}, warnings: []string{}, info: map[string]any{}}
}

func (v *importValidation) fail(message string) {
	v.valid = false
	v.errors = append(v.errors, message)
}

func (v *importValidation) merge(other *importValidation) {
	if !other.valid {
		v.valid = false
	}
	v.errors = append(v.errors, other.errors...)
	v.warnings = append(v.warnings, other.warnings...)
	for key, value := range other.info {
		v.info[key] = value
	}
}

func (v *importValidation) body() map[string]any {
	return map[string]any{"is_valid": v.valid, "errors": v.errors, "warnings": v.warnings, "info": v.info}
}

// pythonRound2 is round(value, 2) (correctly rounded, like CPython).
func pythonRound2(value float64) float64 {
	rounded, _ := strconv.ParseFloat(strconv.FormatFloat(value, 'f', 2, 64), 64)
	return rounded
}

func importLookup(object domain.RequestObject, key string) any {
	value, _ := object.Lookup(key)
	return value
}

// importValidationFailure is an exception validate_import catches and answers
// as 500 {'error': 'Errore durante la validazione: ' + str(e)}: a manifest or
// data file whose JSON value shape makes Python raise outside the validator's
// own except clauses, or a UUIDField ValidationError from a lookup.
type importValidationFailure struct{ text string }

func (e *importValidationFailure) Error() string { return e.text }

// importArchiveFailure is an I/O error of the server's own temporary copy of
// the upload while the ZIP reader reads it (Django reads the same file): not
// an archive property, the 500 page.
type importArchiveFailure struct{ err error }

func (e *importArchiveFailure) Error() string { return "archive: " + e.err.Error() }
func (e *importArchiveFailure) Unwrap() error { return e.err }

// importArchiveError is a ZIP reader failure: the Python exception zipfile,
// zlib, bz2 or lzma raised (its Error() is str(e), which the stage's except
// branch renders), or an importArchiveFailure.
func importArchiveError(err error) error {
	var exception *pyzipfile.Exception
	if errors.As(err, &exception) {
		return exception
	}
	return &importArchiveFailure{err: err}
}

func importIsArchiveError(err error) bool {
	var failure *importArchiveFailure
	return errors.As(err, &failure)
}

// importMemberJSON is json.loads(zf.read(name)).
func importMemberJSON(archive *consumerImportArchive, name string) (any, error) {
	data, err := archive.read(name)
	if err != nil {
		return nil, importArchiveError(err)
	}
	return importJSONParse(data)
}

// importJSONReadMessage is validate_manifest's and validate_owner_identity's
// except pair: a json.JSONDecodeError has its own text, every other exception
// (UnicodeDecodeError, the int() ValueError, RecursionError, AttributeError)
// the generic one.
func importJSONReadMessage(manifest bool, err error) string {
	var decodeError *importJSONDecodeError
	if errors.As(err, &decodeError) {
		if manifest {
			return "Invalid JSON in manifest: " + decodeError.Error()
		}
		return "Invalid JSON in data files: " + decodeError.Error()
	}
	if manifest {
		return "Error reading manifest: " + err.Error()
	}
	return "Error reading owner identity: " + err.Error()
}

// importManifestShapeFailure is validate_manifest's exception for a manifest
// that is not an object: `field not in manifest` raises TypeError for an
// int/float/bool/None, and manifest.get raises AttributeError for a str/list.
// Both escape validate_all and reach the view's 500.
func importManifestShapeFailure(value any) error {
	kind := importJSONTypeName(value)
	switch value.(type) {
	case string, []any:
		return &importValidationFailure{text: "'" + kind + "' object has no attribute 'get'"}
	}
	return &importValidationFailure{text: "argument of type '" + kind + "' is not iterable"}
}

// validateArchive is ImportValidator(path).validate_all(): Django's value (a
// ValidationResult, or the exception text of the view's 500) for every
// archive, a ZIP reader exception included (internal/pyzipfile).
func (s *AssociationTransfer) validateArchive(ctx context.Context, path string) (*importValidation, error) {
	result := newImportValidation()
	// validate_file
	file := newImportValidation()
	isZip, err := consumerImportIsZipFile(path)
	if err != nil {
		if failure := importArchiveError(err); !importIsArchiveError(failure) {
			// is_zipfile catches OSError only: a BadZipFile from the ZIP64
			// records escapes validate_all, the view's 500 JSON.
			return nil, &importValidationFailure{text: failure.Error()}
		}
		return nil, err
	}
	if !isZip {
		file.fail("Not a valid ZIP file: " + path)
		result.merge(file)
		return result, nil
	}
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	file.info["file_size_bytes"] = info.Size()
	file.info["file_size_mb"] = dto.PythonFloat(pythonRound2(float64(info.Size()) / (1024 * 1024)))
	result.merge(file)

	archive, err := consumerImportOpenArchive(path)
	if err != nil {
		failure := importArchiveError(err)
		if importIsArchiveError(failure) {
			return nil, err
		}
		// validate_manifest opens the ZipFile inside its try: except Exception.
		manifestResult := newImportValidation()
		manifestResult.fail("Error reading manifest: " + failure.Error())
		result.merge(manifestResult)
		return result, nil
	}
	defer archive.Close()

	// validate_manifest
	manifestResult := newImportValidation()
	if !archive.has("manifest.json") {
		manifestResult.fail("Missing manifest.json in export file")
		result.merge(manifestResult)
		return result, nil
	}
	decoded, err := importMemberJSON(archive, "manifest.json")
	if err != nil {
		if importIsArchiveError(err) || errors.Is(err, dto.ErrJSONRecursionBand) {
			return nil, err
		}
		manifestResult.fail(importJSONReadMessage(true, err))
		result.merge(manifestResult)
		return result, nil
	}
	manifest, isObject := decoded.(domain.RequestObject)
	if !isObject {
		return nil, importManifestShapeFailure(decoded)
	}
	for _, field := range []string{"version", "export_format", "export_date", "association"} {
		if _, present := manifest.Lookup(field); !present {
			manifestResult.fail("Missing required manifest field: " + field)
		}
	}
	format := importLookup(manifest, "export_format")
	if text, ok := format.(string); !ok || text != "bakney_sport_export_v1" {
		manifestResult.fail("Unsupported export format: " + importJSONStr(format))
	}
	association, present := manifest.Lookup("association")
	if !present {
		association = domain.RequestObject{}
	}
	for _, item := range []struct {
		key   string
		value any
	}{{"export_format", format}, {"export_date", importLookup(manifest, "export_date")}, {"association", association}} {
		if manifestResult.info[item.key], err = transferJSON(item.value); err != nil {
			return nil, err
		}
	}
	result.merge(manifestResult)
	if !manifestResult.valid {
		return result, nil
	}

	// validate_data_files
	dataResult := newImportValidation()
	var models any = []any{}
	if value, present := manifest.Lookup("models_exported"); present {
		models = value
	}
	items, err := importJSONIterate(models)
	if err != nil {
		return nil, &importValidationFailure{text: err.Error()}
	}
	expected := []any{}
	for _, item := range items {
		model, ok := item.(domain.RequestObject)
		if !ok {
			return nil, &importValidationFailure{text: (&importJSONAttributeError{kind: importJSONTypeName(item), attribute: "get"}).Error()}
		}
		filename := importLookup(model, "file")
		if !importJSONTruthy(filename) {
			continue
		}
		contains, err := importJSONContains(filename, "/")
		if err != nil {
			return nil, &importValidationFailure{text: err.Error()}
		}
		if contains {
			expected = append(expected, filename)
		} else {
			expected = append(expected, "data/"+importJSONStr(filename))
		}
	}
	missing := 0
	for _, name := range expected {
		if !importJSONNameListHas(archive.names, name) {
			missing++
			dataResult.warnings = append(dataResult.warnings, "Missing data file: "+importJSONStr(name))
		}
	}
	dataResult.info["expected_files"] = len(expected)
	dataResult.info["missing_files"] = missing
	result.merge(dataResult)

	// validate_owner_identity
	identity, owner, err := s.validateOwnerIdentity(archive)
	if err != nil {
		return nil, err
	}
	result.merge(identity)
	if identity.valid {
		conflicts, keeps, err := s.validateConflicts(ctx, owner)
		if err != nil {
			return nil, err
		}
		result.merge(conflicts)
		if keeps {
			result.info["owner_user"].(map[string]any)["requires_recovery_password"] = false
		}
	}
	return result, nil
}

// importOwnerIdentity is what validate_owner_identity keeps for the conflicts:
// the raw sport_association_id and user_id (str() of any JSON value) and the
// str() of every non-None user_id the users file lists.
type importOwnerIdentity struct {
	association, owner any
	users              []string
}

// validateOwnerIdentity is validate_owner_identity.
func (s *AssociationTransfer) validateOwnerIdentity(archive *consumerImportArchive) (*importValidation, *importOwnerIdentity, error) {
	result := newImportValidation()
	if !archive.has("data/01_sport_association.json") {
		result.fail("Missing required data file: 01_sport_association.json")
		return result, nil, nil
	}
	decoded, err := importMemberJSON(archive, "data/01_sport_association.json")
	if err != nil {
		if importIsArchiveError(err) || errors.Is(err, dto.ErrJSONRecursionBand) {
			return nil, nil, err
		}
		result.fail(importJSONReadMessage(false, err))
		return result, nil, nil
	}
	associations, ok := decoded.([]any)
	if !ok || len(associations) != 1 {
		result.fail("Export must contain exactly one SportAssociation record in data/01_sport_association.json")
		return result, nil, nil
	}
	association, ok := associations[0].(domain.RequestObject)
	if !ok {
		result.fail(importJSONReadMessage(false, &importJSONAttributeError{kind: importJSONTypeName(associations[0]), attribute: "get"}))
		return result, nil, nil
	}
	associationID := importLookup(association, "sport_association_id")
	if !importJSONTruthy(associationID) {
		result.fail("SportAssociation record is missing sport_association_id")
		return result, nil, nil
	}
	ownerValue := importLookup(association, "user_id")
	if !importJSONTruthy(ownerValue) {
		result.fail("SportAssociation record is missing user_id for owner selection")
		return result, nil, nil
	}
	ownerText := importJSONStr(ownerValue)
	if !archive.has("data/02_users.json") {
		result.fail("Missing required data file: 02_users.json")
		return result, nil, nil
	}
	// with zf.open('data/02_users.json') as stream: iter_json_array(stream):
	// the member is read in iter_json_array's chunks, so a reader exception
	// (zlib, CRC, EOFError) comes after the items read before it, inside
	// validate_owner_identity's except Exception.
	users, err := archive.openMember("data/02_users.json")
	if err != nil {
		failure := importArchiveError(err)
		if importIsArchiveError(failure) {
			return nil, nil, err
		}
		result.fail(importJSONReadMessage(false, failure))
		return result, nil, nil
	}
	defer users.Close()
	var matches []domain.RequestObject
	seen := map[string]bool{}
	var userIDs []string
	err = importIterJSONArray(users, importJSONChunkSize, func(item any) error {
		user, ok := item.(domain.RequestObject)
		if !ok {
			return &importJSONAttributeError{kind: importJSONTypeName(item), attribute: "get"}
		}
		id := importLookup(user, "user_id")
		if id != nil {
			text := importJSONStr(id)
			if !seen[text] {
				seen[text] = true
				userIDs = append(userIDs, text)
			}
		}
		if importJSONStr(id) == ownerText {
			matches = append(matches, user)
		}
		return nil
	})
	if errors.Is(err, dto.ErrJSONRecursionBand) {
		return nil, nil, err
	}
	if err != nil {
		result.fail(importJSONReadMessage(false, err))
		return result, nil, nil
	}
	if len(matches) != 1 {
		result.fail("Expected exactly one User with user_id " + ownerText + " matching SportAssociation.user_id")
		return result, nil, nil
	}
	owner := matches[0]
	rendered := map[string]any{}
	for _, key := range []string{"sport_association_id", "denomination", "tax_code"} {
		value, err := transferJSON(importLookup(association, key))
		if err != nil {
			return nil, nil, err
		}
		rendered[key] = value
	}
	result.info["association"] = rendered
	ownerInfo := map[string]any{"requires_recovery_password": !importRestorablePasswordHash(importLookup(owner, "password"))}
	for _, key := range []string{"user_id", "username", "email"} {
		value, err := transferJSON(importLookup(owner, key))
		if err != nil {
			return nil, nil, err
		}
		ownerInfo[key] = value
	}
	result.info["owner_user"] = ownerInfo
	// validate_uuid_conflicts reads the matched owner record's own user_id
	// (self.source_owner.get('user_id')), which str() matched to the
	// association's user_id but can be another value (null for "None").
	return result, &importOwnerIdentity{association: associationID, owner: importLookup(owner, "user_id"), users: userIDs}, nil
}

// importUUIDField is UUIDField.to_python for a lookup value: ints and bools
// become uuid.UUID(int=n), strings uuid.UUID(hex=text), and any other JSON
// value is the ValidationError Django does not catch.
func importUUIDField(value any) (string, bool) {
	switch value.(type) {
	case bool, string, json.Number:
		id, err := dto.AssociationExportDocument(value)
		return id, err == nil
	}
	return "", false
}

// importUUIDFailure is str() of UUIDField.to_python's ValidationError:
// '['“<str(value)>” is not a valid UUID.']'.
func importUUIDFailure(value any) error {
	message := "“" + importJSONStr(value) + "” is not a valid UUID."
	return &importValidationFailure{text: "[" + dto.PythonStringRepr(message) + "]"}
}

// validateConflicts is validate_uuid_conflicts; keeps is the popped
// existing_owner_keeps_credentials flag.
func (s *AssociationTransfer) validateConflicts(ctx context.Context, identity *importOwnerIdentity) (*importValidation, bool, error) {
	result := newImportValidation()
	association, ok := importUUIDField(identity.association)
	if !ok {
		return nil, false, importUUIDFailure(identity.association)
	}
	if !importJSONTruthy(identity.owner) {
		// `if owner_id:`: an owner record matched through str() of a falsy
		// user_id (null against "None", false against "False", 0, [] or {})
		// skips the owner, its other association and the unlinked users; only
		// the association lookup runs (the owner key is a placeholder whose
		// answers are not read).
		rows, err := s.options.Writes.ImportConflicts(ctx, association, "00000000-0000-0000-0000-000000000000", nil)
		if err != nil {
			return nil, false, err
		}
		if rows.AssociationExists {
			result.fail("Association UUID conflict: " + importJSONStr(identity.association) + " already exists")
		}
		return result, false, nil
	}
	owner, ok := importUUIDField(identity.owner)
	if !ok {
		return nil, false, importUUIDFailure(identity.owner)
	}
	// unlinked_existing_user_ids: sorted({str(id)} - {str(owner)}) then
	// UUIDField.to_python on each, so an invalid candidate is a 500 too.
	ownerText := importJSONStr(identity.owner)
	candidates := make([]string, 0, len(identity.users))
	for _, user := range identity.users {
		if user != ownerText {
			candidates = append(candidates, user)
		}
	}
	sort.Strings(candidates)
	canonical := make([]string, 0, len(candidates))
	for _, user := range candidates {
		id, ok := importUUIDField(user)
		if !ok {
			return nil, false, importUUIDFailure(user)
		}
		canonical = append(canonical, id)
	}
	rows, err := s.options.Writes.ImportConflicts(ctx, association, owner, canonical)
	if err != nil {
		return nil, false, err
	}
	if rows.AssociationExists {
		result.fail("Association UUID conflict: " + importJSONStr(identity.association) + " already exists")
	}
	if rows.OtherAssociation != nil {
		result.fail("Owner user already owns another SportAssociation: " + *rows.OtherAssociation)
	}
	for _, user := range rows.Unlinked {
		result.fail("User UUID collision: " + user + " already exists and is not linked to the archive owner")
	}
	return result, rows.OwnerExists, nil
}

// importPasswordHashIterations is Django 5.2's PBKDF2PasswordHasher.iterations
// (also the limit is_restorable_password_hash applies: hasher.iterations).
const importPasswordHashIterations = 1_000_000

// importRestorablePasswordHash is is_restorable_password_hash: an archived
// hash Django can authenticate against. identify_hasher resolves the
// algorithms settings.PASSWORD_HASHERS lists (Django's defaults:
// pbkdf2_sha256, pbkdf2_sha1, argon2, bcrypt_sha256 and scrypt); the
// unsalted_sha1 and unsalted_md5 algorithms identify_hasher also names are
// not configured, so get_hasher raises ValueError and the answer is false.
func importRestorablePasswordHash(value any) bool {
	encoded, ok := value.(string)
	if !ok || encoded == "" || strings.HasPrefix(encoded, "!") {
		return false
	}
	switch importIdentifyHasher(encoded) {
	case "pbkdf2_sha256":
		return importPBKDF2PasswordHash(encoded, 32)
	case "pbkdf2_sha1":
		return importPBKDF2PasswordHash(encoded, 20)
	case "argon2":
		return importArgon2PasswordHash(encoded)
	case "bcrypt_sha256":
		return importBCryptPasswordHash(encoded)
	case "scrypt":
		return importScryptPasswordHash(encoded)
	}
	return false
}

// importIdentifyHasher is identify_hasher followed by get_hasher for the
// configured hashers; an empty result is the ValueError that makes
// is_restorable_password_hash false.
func importIdentifyHasher(encoded string) string {
	algorithm, _, _ := strings.Cut(encoded, "$")
	length := len(pystr.Runes(encoded))
	if length == 32 && !strings.Contains(encoded, "$") || length == 37 && strings.HasPrefix(encoded, "sha1$") {
		return ""
	}
	if strings.HasPrefix(encoded, "md5$") {
		return ""
	}
	switch algorithm {
	case "pbkdf2_sha256", "pbkdf2_sha1", "argon2", "bcrypt_sha256", "scrypt":
		return algorithm
	}
	return ""
}

// importCostLimit is the decoded cost field's check: a value at or below zero
// or above the hasher's own limit is not restorable.
func importCostLimit(value *big.Int, limit int64) bool {
	return value.Sign() > 0 && value.Cmp(big.NewInt(limit)) <= 0
}

// importBase64Len is len(base64.b64decode(text, validate=True)): Python 3.13's
// binascii.a2b_base64(strict_mode=True). ok is false for the binascii.Error
// (a ValueError, which is_restorable_password_hash catches).
func importBase64Len(text string) (int, bool) {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	data := []byte(text)
	if len(data) > 0 && data[0] == '=' {
		return 0, false // Leading padding not allowed
	}
	length, quad, pads, padding := 0, 0, 0, false
	for i, ch := range data {
		if ch == '=' {
			padding = true
			if quad == 0 {
				return 0, false // Excess padding not allowed
			}
			if quad < 2 {
				continue
			}
			pads++
			if quad+pads >= 4 {
				if i+1 < len(data) {
					return 0, false // Excess data after padding
				}
				return length, true
			}
			continue
		}
		if strings.IndexByte(alphabet, ch) < 0 {
			return 0, false // Only base64 data is allowed
		}
		if padding {
			return 0, false // Discontinuous padding not allowed
		}
		pads = 0
		switch quad {
		case 1, 2:
			length++
		case 3:
			length++
		}
		quad = (quad + 1) % 4
	}
	if quad == 1 {
		return 0, false
	}
	if quad != 0 {
		return 0, false // Incorrect padding
	}
	return length, true
}

// importLooseBase64 is base64.b64decode(text) without validation (Python
// 3.13's a2b_base64 with strict_mode=False): characters outside the alphabet
// are skipped, and '=' only closes an incomplete final group.
func importLooseBase64(text string) ([]byte, error) {
	for i := 0; i < len(text); i++ {
		if text[i] >= 0x80 {
			return nil, errors.New("string argument should contain only ASCII characters")
		}
	}
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	out := make([]byte, 0, len(text)*3/4)
	quad, pads := 0, 0
	var left byte
	for i := 0; i < len(text); i++ {
		ch := text[i]
		if ch == '=' {
			if quad >= 2 {
				pads++
			}
			continue
		}
		value := strings.IndexByte(alphabet, ch)
		if value < 0 {
			continue
		}
		pads = 0
		v := byte(value)
		switch quad {
		case 0:
			quad, left = 1, v
		case 1:
			out = append(out, left<<2|v>>4)
			quad, left = 2, v&0x0f
		case 2:
			out = append(out, left<<4|v>>2)
			quad, left = 3, v&0x03
		case 3:
			out = append(out, left<<6|v)
			quad, left = 0, 0
		}
	}
	if quad != 0 && (quad == 1 || quad+pads < 4) {
		return nil, errors.New("invalid base64 padding")
	}
	return out, nil
}

// importDecodedStrLen is argon2's _decoded_str_len: the byte length an
// encoded string of this length stands for.
func importDecodedStrLen(length int) int {
	last := 0
	switch length % 4 {
	case 3:
		last = 2
	case 2:
		last = 1
	}
	return length/4*3 + last
}

// importPBKDF2PasswordHash is PBKDF2PasswordHasher.decode and
// is_restorable_password_hash's pbkdf2_ branch, for the digest size of the
// configured hasher (32 for sha256, 20 for sha1).
func importPBKDF2PasswordHash(encoded string, size int) bool {
	parts := strings.SplitN(encoded, "$", 4)
	if len(parts) != 4 {
		return false // encoded.split("$", 3): not exactly four fields
	}
	iterations, ok := dto.PythonIntText(parts[1]) // int(iterations)
	if !ok {
		return false
	}
	salt, digest := parts[2], parts[3]
	if salt == "" || digest == "" {
		return false
	}
	if !importCostLimit(iterations, importPasswordHashIterations) {
		return false
	}
	length, ok := importBase64Len(digest)
	return ok && length == size
}

// importScryptPasswordHash is ScryptPasswordHasher.decode and the scrypt
// branch: a work factor above one and a power of two, a 64-byte digest.
func importScryptPasswordHash(encoded string) bool {
	parts := strings.SplitN(encoded, "$", 6)
	if len(parts) != 6 {
		return false
	}
	workFactor, ok := dto.PythonIntText(parts[1])
	if !ok {
		return false
	}
	salt := parts[2]
	blockSize, ok := dto.PythonIntText(parts[3])
	if !ok {
		return false
	}
	parallelism, ok := dto.PythonIntText(parts[4])
	if !ok {
		return false
	}
	digest := parts[5]
	if salt == "" || digest == "" {
		return false
	}
	// work_factor (2**14), block_size (8) and parallelism (5) are the
	// hasher's own values, the cost limits above.
	if !importCostLimit(workFactor, 1<<14) || !importCostLimit(blockSize, 8) || !importCostLimit(parallelism, 5) {
		return false
	}
	if workFactor.Cmp(big.NewInt(1)) <= 0 {
		return false
	}
	if new(big.Int).And(workFactor, new(big.Int).Sub(workFactor, big.NewInt(1))).Sign() != 0 {
		return false
	}
	length, ok := importBase64Len(digest)
	return ok && length == 64
}

// importBCryptPasswordHash is BCryptSHA256PasswordHasher.decode and the
// bcrypt branch: algostr 2a/2b/2y, work_factor within rounds (12), a
// 22-character salt and 31-character checksum from the bcrypt alphabet.
func importBCryptPasswordHash(encoded string) bool {
	parts := strings.SplitN(encoded, "$", 5)
	if len(parts) != 5 {
		return false
	}
	algostr, data := parts[2], pystr.Runes(parts[4])
	workFactor, ok := dto.PythonIntText(parts[3])
	if !ok {
		return false
	}
	salt := pystr.String(data[:min(22, len(data))])
	checksum := ""
	if len(data) > 22 {
		checksum = pystr.String(data[22:])
	}
	if salt == "" || checksum == "" {
		return false
	}
	if !importCostLimit(workFactor, 12) {
		return false
	}
	if algostr != "2a" && algostr != "2b" && algostr != "2y" {
		return false
	}
	if workFactor.Cmp(big.NewInt(4)) < 0 {
		return false
	}
	if len(data) != 22+31 {
		return false
	}
	const bcryptChars = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
	for _, r := range data {
		if !strings.ContainsRune(bcryptChars, r) {
			return false
		}
	}
	return true
}

// importArgon2PasswordHash is Argon2PasswordHasher.decode (argon2-cffi's
// extract_parameters over the PHC string) and the argon2 branch: the decoded
// digest's length matches the hash_len the string itself names.
func importArgon2PasswordHash(encoded string) bool {
	_, rest, _ := strings.Cut(encoded, "$")
	parts := strings.Split("$"+rest, "$")
	if len(parts) == 5 {
		// Argon v1.2 hashes carry no v= field: extract_parameters inserts one.
		parts = append(parts[:2], append([]string{"v=18"}, parts[2:]...)...)
	}
	if len(parts) != 6 || parts[0] != "" {
		return false
	}
	switch parts[1] {
	case "argon2i", "argon2d", "argon2id":
	default:
		return false
	}
	values := map[string]*big.Int{}
	for _, segment := range append([]string{parts[2]}, strings.Split(parts[3], ",")...) {
		name, text, found := strings.Cut(segment, "=")
		if !found || strings.Contains(text, "=") {
			return false
		}
		value, ok := dto.PythonIntText(text)
		if !ok {
			return false
		}
		values[name] = value
	}
	if len(values) != 4 || values["v"] == nil || values["m"] == nil || values["t"] == nil || values["p"] == nil {
		return false
	}
	// variety, *_, b64salt, hash = rest.split("$")
	restParts := strings.Split(rest, "$")
	if len(restParts) < 3 {
		return false
	}
	b64salt, digest := restParts[len(restParts)-2], restParts[len(restParts)-1]
	if digest == "" {
		return false
	}
	hashLen := importDecodedStrLen(len(pystr.Runes(digest)))
	salt, err := importLooseBase64(b64salt + strings.Repeat("=", (4-len(b64salt)%4)%4))
	if err != nil || len(salt) == 0 {
		return false
	}
	if !importCostLimit(values["t"], 2) || !importCostLimit(values["m"], 102400) || !importCostLimit(values["p"], 8) {
		return false
	}
	length, ok := importBase64Len(digest + strings.Repeat("=", (4-len(digest)%4)%4))
	return ok && length == hashLen
}

// exportSize is list_exports' default_storage.size(filepath) and the save of
// the size; any storage failure is logged by Django and leaves the size null.
func (s *AssociationTransfer) exportSize(ctx context.Context, document, filepath string, audit domain.LoginAuditContext) (*int64, error) {
	key, err := documentkey.S3Key(filepath, s.options.Objects.Location())
	if err != nil {
		return nil, nil
	}
	size, err := s.options.Objects.Size(ctx, key)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, nil
	}
	if size < 0 {
		// S3 reports Content-Length, which HTTP cannot make negative.
		return nil, domain.ErrAssociationTransferUnexpected
	}
	var actorAssociation *string
	if audit.ActorID != nil {
		if actorAssociation, err = s.options.Writes.ActorAssociation(ctx, *audit.ActorID); err != nil {
			return nil, err
		}
	}
	if err := s.options.Writes.SetExportSize(ctx, document, size, actorAssociation, audit, s.now()); err != nil {
		return nil, err
	}
	return &size, nil
}
