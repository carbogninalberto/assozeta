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
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"golang.org/x/text/encoding/charmap"
)

// AssociationTransferWrites is the state the dispatching export/import paths
// change: the export rows, Django's cache and the validator's lookups.
type AssociationTransferWrites interface {
	ExportFiles(ctx context.Context, association, document string) ([]repository.AssociationExportFile, error)
	DocumentReferences(ctx context.Context, id string) (int64, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	DeleteArchive(ctx context.Context, id, association string, audit domain.LoginAuditContext, at time.Time) error
	DeleteDocument(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error
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
		return nil, domain.ErrAssociationTransferNotPorted
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
			return nil, domain.ErrAssociationTransferNotPorted
		}
		return dto.PythonFloat(v), nil
	case json.Number:
		if number, err := strconv.ParseFloat(string(v), 64); err != nil || math.IsInf(number, 0) {
			if _, ok := new(big.Int).SetString(string(v), 10); !ok {
				return nil, domain.ErrAssociationTransferNotPorted
			}
		}
		return v, nil
	case nil, string, bool, int64:
		return v, nil
	}
	return nil, domain.ErrAssociationTransferNotPorted
}

// taskReady is AsyncResult(task_id).ready() on the django-db result backend:
// the stored status is SUCCESS, FAILURE or REVOKED. Rows whose decoding
// rebuilds exceptions or merges meta keys are refused, as in ImportStatus.
func (s *AssociationTransfer) taskReady(ctx context.Context, task string) (bool, error) {
	row, err := s.store.TaskResult(ctx, task)
	if err != nil || row == nil {
		return false, err
	}
	switch row.Status {
	case "FAILURE", "REVOKED", "RETRY":
		return false, domain.ErrAssociationTransferNotPorted
	}
	if row.Encoding != "utf-8" {
		return false, domain.ErrAssociationTransferNotPorted
	}
	if row.Meta != nil && *row.Meta != "" {
		meta, err := celeryJSON(*row.Meta)
		if err != nil {
			return false, err
		}
		object, ok := meta.(map[string]any)
		if !ok {
			return false, domain.ErrAssociationTransferNotPorted
		}
		children, _ := object["children"].([]any)
		_, status := object["status"]
		if status || len(children) > 0 || (object["children"] != nil && children == nil) {
			return false, domain.ErrAssociationTransferNotPorted
		}
	}
	if row.Result != nil && *row.Result != "" {
		if _, err := celeryJSON(*row.Result); err != nil {
			return false, err
		}
	}
	return row.Status == "SUCCESS", nil
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
		return nil, domain.ErrAssociationTransferNotPorted
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
	// Document.delete()'s collector over other references is not ported.
	references, err := s.options.Writes.DocumentReferences(ctx, row.DocumentID)
	if err != nil {
		return nil, err
	}
	if references > 0 {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	var key string
	if row.Filepath != nil && *row.Filepath != "" {
		if key, err = documentkey.S3Key(*row.Filepath, s.options.Objects.Location()); err != nil {
			return nil, domain.ErrAssociationTransferNotPorted
		}
	}
	var actorAssociation *string
	if audit.ActorID != nil {
		if actorAssociation, err = s.options.Writes.ActorAssociation(ctx, *audit.ActorID); err != nil {
			return nil, err
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
	if err = s.options.Writes.DeleteDocument(ctx, row.DocumentID, actorAssociation, audit, s.now()); err != nil {
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
		return nil, domain.ErrAssociationTransferNotPorted // a file is not JSON serializable for the task
	}
	name := "temp/imports/import_" + s.options.NewID() + ".zip"
	key, err := documentkey.S3Key(name, s.options.Objects.Location())
	if err != nil {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	if !s.options.FileOverwrite {
		exists, err := s.options.Objects.Exists(ctx, key)
		if err != nil {
			return nil, err
		}
		if exists {
			return nil, domain.ErrAssociationTransferNotPorted // get_available_name picks another name
		}
	}
	guessed, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return nil, domain.ErrAssociationTransferNotPorted
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
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
	defer cancel()
	id, err := s.options.Tasks.Publish(publish, celery.Task{Name: "import_association_data", Kwargs: celery.Dict{
		{Key: "zip_file_path", Value: name}, {Key: "owner_password", Value: password}}})
	if err != nil {
		// Django deletes the saved archive and answers its own 500 with the exception text.
		_ = s.options.Objects.Delete(context.WithoutCancel(ctx), key)
		return nil, err
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

// ImportValidate mirrors validate_import: the upload is written to
// <mkdtemp>/<name> and checked by ImportValidator.validate_all().
func (s *AssociationTransfer) ImportValidate(ctx context.Context, upload AssociationImportUpload) (*AssociationTransferReply, error) {
	if upload.File == nil {
		return fileRequired, nil
	}
	if len(upload.File.Name) > 255 {
		return nil, domain.ErrAssociationTransferNotPorted // OSError text in Django's own 500 answer
	}
	dir, err := pythonMkdtemp()
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	path := dir + "/" + upload.File.Name
	if err = os.WriteFile(path, upload.File.Content, 0o644); err != nil {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	result, err := s.validateArchive(ctx, path)
	if err != nil {
		return nil, err
	}
	return &AssociationTransferReply{Status: 200, Body: result.body()}, nil
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

// validateArchive is ImportValidator(path).validate_all(). Inputs whose
// Python exception text would reach the answer (undecodable JSON, values of
// unexpected types, archives the zipfile port does not read) are refused.
func (s *AssociationTransfer) validateArchive(ctx context.Context, path string) (*importValidation, error) {
	result := newImportValidation()
	// validate_file
	file := newImportValidation()
	isZip, err := consumerImportIsZipFile(path)
	if err != nil {
		return nil, domain.ErrAssociationTransferNotPorted
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
		return nil, domain.ErrAssociationTransferNotPorted
	}
	defer archive.Close()
	readJSON := func(name string) (any, error) {
		data, err := archive.read(name)
		if err != nil {
			return nil, domain.ErrAssociationTransferNotPorted
		}
		value, err := consumerImportJSON(data)
		if err != nil {
			return nil, domain.ErrAssociationTransferNotPorted
		}
		return value, nil
	}

	// validate_manifest
	manifestResult := newImportValidation()
	if !archive.has("manifest.json") {
		manifestResult.fail("Missing manifest.json in export file")
		result.merge(manifestResult)
		return result, nil
	}
	decoded, err := readJSON("manifest.json")
	if err != nil {
		return nil, err
	}
	manifest, ok := decoded.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	for _, field := range []string{"version", "export_format", "export_date", "association"} {
		if _, present := manifest.Lookup(field); !present {
			manifestResult.fail("Missing required manifest field: " + field)
		}
	}
	format := importLookup(manifest, "export_format")
	if text, ok := format.(string); !ok || text != "bakney_sport_export_v1" {
		if format != nil {
			if _, ok := format.(string); !ok {
				return nil, domain.ErrAssociationTransferNotPorted // str() of other JSON values
			}
		}
		manifestResult.fail("Unsupported export format: " + dto.PythonStr(format))
	}
	association, present := manifest.Lookup("association")
	if !present {
		association = domain.RequestObject{}
	}
	for key, value := range map[string]any{"export_format": format, "export_date": importLookup(manifest, "export_date"),
		"association": association} {
		if manifestResult.info[key], err = transferJSON(value); err != nil {
			return nil, err
		}
	}
	result.merge(manifestResult)
	if !manifestResult.valid {
		return result, nil
	}

	// validate_data_files
	dataResult := newImportValidation()
	models := []any{}
	if value, present := manifest.Lookup("models_exported"); present {
		list, ok := value.([]any)
		if !ok {
			return nil, domain.ErrAssociationTransferNotPorted
		}
		models = list
	}
	var expected []string
	for _, item := range models {
		model, ok := item.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrAssociationTransferNotPorted
		}
		name := importLookup(model, "file")
		if !importTruthy(name) {
			continue
		}
		text, ok := name.(string)
		if !ok {
			return nil, domain.ErrAssociationTransferNotPorted
		}
		if containsByte(text, '/') {
			expected = append(expected, text)
		} else {
			expected = append(expected, "data/"+text)
		}
	}
	missing := 0
	for _, name := range expected {
		if !archive.has(name) {
			missing++
			dataResult.warnings = append(dataResult.warnings, "Missing data file: "+name)
		}
	}
	dataResult.info["expected_files"] = len(expected)
	dataResult.info["missing_files"] = missing
	result.merge(dataResult)

	// validate_owner_identity
	identity, owner, err := s.validateOwnerIdentity(archive, readJSON)
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

func containsByte(text string, b byte) bool {
	for i := 0; i < len(text); i++ {
		if text[i] == b {
			return true
		}
	}
	return false
}

// importOwnerIdentity is what validate_owner_identity keeps for the conflicts.
type importOwnerIdentity struct {
	association, owner string
	users              []string
}

// importText is str(value) for the archive's identifiers: only strings are
// read (other JSON values reach Django's database lookups or answers as reprs).
func importText(value any) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", domain.ErrAssociationTransferNotPorted
	}
	return text, nil
}

func (s *AssociationTransfer) validateOwnerIdentity(archive *consumerImportArchive, readJSON func(string) (any, error)) (*importValidation, *importOwnerIdentity, error) {
	result := newImportValidation()
	if !archive.has("data/01_sport_association.json") {
		result.fail("Missing required data file: 01_sport_association.json")
		return result, nil, nil
	}
	decoded, err := readJSON("data/01_sport_association.json")
	if err != nil {
		return nil, nil, err
	}
	associations, ok := decoded.([]any)
	if !ok || len(associations) != 1 {
		result.fail("Export must contain exactly one SportAssociation record in data/01_sport_association.json")
		return result, nil, nil
	}
	association, ok := associations[0].(domain.RequestObject)
	if !ok {
		return nil, nil, domain.ErrAssociationTransferNotPorted
	}
	associationID := importLookup(association, "sport_association_id")
	if !importTruthy(associationID) {
		result.fail("SportAssociation record is missing sport_association_id")
		return result, nil, nil
	}
	ownerValue := importLookup(association, "user_id")
	if !importTruthy(ownerValue) {
		result.fail("SportAssociation record is missing user_id for owner selection")
		return result, nil, nil
	}
	ownerText, err := importText(ownerValue)
	if err != nil || ownerText == "None" {
		return nil, nil, domain.ErrAssociationTransferNotPorted
	}
	if !archive.has("data/02_users.json") {
		result.fail("Missing required data file: 02_users.json")
		return result, nil, nil
	}
	decoded, err = readJSON("data/02_users.json")
	if err != nil {
		return nil, nil, err
	}
	users, ok := decoded.([]any)
	if !ok {
		return nil, nil, domain.ErrAssociationTransferNotPorted
	}
	var matches []domain.RequestObject
	seen := map[string]bool{}
	var userIDs []string
	for _, item := range users {
		user, ok := item.(domain.RequestObject)
		if !ok {
			return nil, nil, domain.ErrAssociationTransferNotPorted
		}
		id := importLookup(user, "user_id")
		if id == nil {
			continue // str(None) never equals a string owner id other than 'None'
		}
		text, err := importText(id)
		if err != nil {
			return nil, nil, err
		}
		if !seen[text] {
			seen[text] = true
			userIDs = append(userIDs, text)
		}
		if text == ownerText {
			matches = append(matches, user)
		}
	}
	if len(matches) != 1 {
		result.fail("Expected exactly one User with user_id " + ownerText + " matching SportAssociation.user_id")
		return result, nil, nil
	}
	owner := matches[0]
	associationText, err := importText(associationID)
	if err != nil {
		return nil, nil, err
	}
	rendered := map[string]any{}
	for _, key := range []string{"sport_association_id", "denomination", "tax_code"} {
		if rendered[key], err = transferJSON(importLookup(association, key)); err != nil {
			return nil, nil, err
		}
	}
	result.info["association"] = rendered
	requires := true
	if password, ok := importLookup(owner, "password").(string); ok {
		restorable, supported := restorablePasswordHash(password)
		if !supported {
			return nil, nil, domain.ErrAssociationTransferNotPorted
		}
		requires = !restorable
	}
	ownerInfo := map[string]any{"requires_recovery_password": requires}
	for _, key := range []string{"user_id", "username", "email"} {
		if ownerInfo[key], err = transferJSON(importLookup(owner, key)); err != nil {
			return nil, nil, err
		}
	}
	result.info["owner_user"] = ownerInfo
	return result, &importOwnerIdentity{association: associationText, owner: ownerText, users: userIDs}, nil
}

// validateConflicts is validate_uuid_conflicts; keeps is the popped
// existing_owner_keeps_credentials flag.
func (s *AssociationTransfer) validateConflicts(ctx context.Context, identity *importOwnerIdentity) (*importValidation, bool, error) {
	result := newImportValidation()
	canonical := func(text string) (string, error) {
		id, err := dto.AssociationExportDocument(text)
		if err != nil {
			return "", domain.ErrAssociationTransferNotPorted // UUIDField ValidationError in Django's own 500 answer
		}
		return id, nil
	}
	association, err := canonical(identity.association)
	if err != nil {
		return nil, false, err
	}
	owner, err := canonical(identity.owner)
	if err != nil {
		return nil, false, err
	}
	var candidates []string
	for _, user := range identity.users {
		if user == identity.owner {
			continue
		}
		id, err := canonical(user)
		if err != nil {
			return nil, false, err
		}
		candidates = append(candidates, id)
	}
	rows, err := s.options.Writes.ImportConflicts(ctx, association, owner, candidates)
	if err != nil {
		return nil, false, err
	}
	if rows.AssociationExists {
		result.fail("Association UUID conflict: " + identity.association + " already exists")
	}
	if rows.OtherAssociation != nil {
		result.fail("Owner user already owns another SportAssociation: " + *rows.OtherAssociation)
	}
	for _, user := range rows.Unlinked {
		result.fail("User UUID collision: " + user + " already exists and is not linked to the archive owner")
	}
	return result, rows.OwnerExists, nil
}
