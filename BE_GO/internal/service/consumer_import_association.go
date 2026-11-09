package service

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/jmoiron/sqlx"
	"golang.org/x/crypto/pbkdf2"
)

// ConsumerImportAssociationTask is application.tasks.import_association_data's Celery name.
const ConsumerImportAssociationTask = "import_association_data"

// ConsumerImportObjects is the S3 default storage the import reads the
// uploaded archive from, writes the media to and deletes the archive from.
type ConsumerImportObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
	Delete(ctx context.Context, key string) error
	Download(ctx context.Context, key string, destination io.Writer) error
	Missing(err error) bool
}

// ConsumerImportSettings are the core.settings values the task reads.
type ConsumerImportSettings struct {
	TempDir       string
	PublicBaseURL string
	Overwrite     bool
}

// ConsumerImportAssociation runs import_association_data(zip_file_path,
// owner_email=”, owner_password=”, preserve_uuids=True, skip_files=False)
// with AssociationImportService.validate() and import_all().
//
// Plan stages the archive, validates it and performs the whole database part
// of import_all() inside one open transaction (the ORM effects come from
// internal/importeffects), checking each media member and its storage name.
// Anything the port does not reproduce, any read error and any database error
// rolls the transaction back and hands the message to Django. Run (after
// CoordinatedTask admission) uploads the media, commits, deletes the staged
// archive from storage and returns Django's result.
type ConsumerImportAssociation struct {
	plan     *importeffects.Plan
	db       *sqlx.DB
	objects  ConsumerImportObjects
	audit    importeffects.AuditWriter
	settings ConsumerImportSettings
	now      func() time.Time
	ids      func() string
}

func NewConsumerImportAssociation(db *sqlx.DB, objects ConsumerImportObjects, audit importeffects.AuditWriter,
	settings ConsumerImportSettings, now func() time.Time, ids func() string) (*ConsumerImportAssociation, error) {
	plan, err := importeffects.LoadPlan()
	if err != nil {
		return nil, err
	}
	return &ConsumerImportAssociation{plan: plan, db: db, objects: objects, audit: audit, settings: settings, now: now, ids: ids}, nil
}

func (s *ConsumerImportAssociation) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.prepare, Run: s.run, Cleanup: s.release}
}

// consumerImportMedia is one storage write of the media pass.
type consumerImportMedia struct {
	member, key, contentType, encoding string
}

type consumerImportCall struct {
	archiveKey string
	tempPath   string
	tx         *sqlx.Tx
	// result is the task's return value; media the uploads Run performs
	// before committing (empty for a failure result).
	result celery.Dict
	media  []consumerImportMedia
	failed bool
}

func (s *ConsumerImportAssociation) release(request *celeryworker.Request) {
	call, _ := request.State.(*consumerImportCall)
	if call == nil {
		return
	}
	if call.tx != nil {
		_ = call.tx.Rollback()
		call.tx = nil
	}
	if call.tempPath != "" {
		_ = os.Remove(call.tempPath)
	}
}

// consumerImportTempName is tempfile.NamedTemporaryFile(suffix='.zip')'s name.
func consumerImportTempName(dir string) (*os.File, error) {
	const characters = "abcdefghijklmnopqrstuvwxyz0123456789_"
	for attempt := 0; attempt < 100; attempt++ {
		name := make([]byte, 8)
		for i := range name {
			n, err := rand.Int(rand.Reader, big.NewInt(int64(len(characters))))
			if err != nil {
				return nil, err
			}
			name[i] = characters[n.Int64()]
		}
		file, err := os.OpenFile(filepath.Join(dir, "tmp"+string(name)+".zip"), os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o600)
		if errors.Is(err, os.ErrExist) {
			continue
		}
		return file, err
	}
	return nil, errors.New("no temporary name")
}

func (s *ConsumerImportAssociation) prepare(ctx context.Context, request *celeryworker.Request) error {
	values, err := consumerPrintBind([]string{"zip_file_path", "owner_email", "owner_password", "preserve_uuids", "skip_files"}, 1, request)
	if err != nil {
		return err
	}
	name, ok := values["zip_file_path"].(string)
	if !ok {
		return celeryworker.ErrHandoff
	}
	password := ""
	if raw, present := values["owner_password"]; present {
		if password, ok = raw.(string); !ok {
			return celeryworker.ErrHandoff
		}
	}
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil || name == "" {
		return celeryworker.ErrHandoff
	}
	call := &consumerImportCall{archiveKey: key}
	request.State = call
	dir, err := filepath.Abs(s.settings.TempDir)
	if err != nil {
		return celeryworker.ErrHandoff
	}
	temp, err := consumerImportTempName(dir)
	if err != nil {
		return celeryworker.ErrHandoff
	}
	call.tempPath = temp.Name()
	err = s.objects.Download(ctx, key, temp)
	if closeErr := temp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return celeryworker.ErrHandoff
	}
	run := &consumerImportRun{s: s, ctx: ctx, path: call.tempPath, password: password}
	if err := run.execute(call); err != nil {
		if call.tx != nil {
			_ = call.tx.Rollback()
			call.tx = nil
		}
		return celeryworker.ErrHandoff
	}
	return nil
}

func (s *ConsumerImportAssociation) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, _ := request.State.(*consumerImportCall)
	if call == nil {
		return nil, errors.New("consumer import: no plan")
	}
	defer func() {
		// finally: default_storage.delete(zip_file_path), errors ignored.
		_ = s.objects.Delete(ctx, call.archiveKey)
	}()
	if call.failed || call.tx == nil {
		return call.result, nil
	}
	archive, err := consumerImportOpenArchive(call.tempPath)
	if err != nil {
		return nil, s.abort(ctx, call, nil)
	}
	defer archive.Close()
	created := []string{}
	for _, media := range call.media {
		body, err := archive.read(media.member)
		if err == nil {
			err = s.objects.Put(ctx, media.key, body, media.contentType, media.encoding)
		}
		if err != nil {
			return nil, s.abort(ctx, call, created)
		}
		created = append(created, media.key)
	}
	tx := call.tx
	call.tx = nil
	if err := tx.Commit(); err != nil {
		s.cleanupKeys(ctx, created)
		return nil, err
	}
	return call.result, nil
}

// abort is import_all's failure path after a media write: the written keys
// are removed (newest first) and the transaction rolls back. The error text
// Django records (the storage exception) is not reproduced.
func (s *ConsumerImportAssociation) abort(ctx context.Context, call *consumerImportCall, created []string) error {
	s.cleanupKeys(ctx, created)
	if call.tx != nil {
		_ = call.tx.Rollback()
		call.tx = nil
	}
	return errors.New("consumer import: media write failed")
}

func (s *ConsumerImportAssociation) cleanupKeys(ctx context.Context, created []string) {
	seen := map[string]bool{}
	unique := []string{}
	for _, key := range created {
		if !seen[key] {
			seen[key] = true
			unique = append(unique, key)
		}
	}
	for i := len(unique) - 1; i >= 0; i-- {
		_ = s.objects.Delete(ctx, unique[i])
	}
}

// consumerImportPyError is a Python exception the import raises: its str()
// becomes the result's error.
type consumerImportPyError struct{ message string }

func (e *consumerImportPyError) Error() string { return e.message }

func importError(message string) error { return &consumerImportPyError{message: message} }

// consumerImportRun is one AssociationImportService.
type consumerImportRun struct {
	s        *ConsumerImportAssociation
	ctx      context.Context
	path     string
	password string
	effects  *importeffects.Effects
	archive  *consumerImportArchive

	uuidMapping     map[string]string
	importedObjects map[string]map[string]*importeffects.Instance
	importedModels  map[string]bool
	sourcePKs       map[string]map[string]bool
	reusedUserPKs   map[string]bool
	documentMapping map[string]*importeffects.Instance
	modelCache      map[string]bool
	stats           celery.Dict
	skipped         celery.Dict
	warnings        []string
	deferredFKs     []consumerImportDeferred
	deferredM2M     []consumerImportDeferredM2M
	ownerUser       *importeffects.Instance
	association     *importeffects.Instance
	associationData domain.RequestObject
	ownerData       domain.RequestObject
	associationID   string
	ownerUserID     string
	media           []consumerImportMedia
	parsed          map[string][]any
}

type consumerImportDeferred struct {
	model, pk, field, target string
}

type consumerImportDeferredM2M struct {
	model, pk, field string
	related          []any
}

func (r *consumerImportRun) setStat(name string, value int64) {
	for i, item := range r.stats {
		if item.Key == name {
			r.stats[i].Value = value
			return
		}
	}
	r.stats = append(r.stats, celery.Item{Key: name, Value: value})
}

func importFailureResult(message string) celery.Dict {
	return celery.Dict{{Key: "success", Value: false}, {Key: "error", Value: message}}
}

// execute is the task body up to the commit. A nil error means call holds
// the outcome; an error hands the message off.
func (r *consumerImportRun) execute(call *consumerImportCall) error {
	errorsList, err := r.validate()
	if r.archive != nil {
		defer r.archive.Close()
	}
	if err != nil {
		return err
	}
	if len(errorsList) > 0 {
		items := make([]any, len(errorsList))
		for i, item := range errorsList {
			items[i] = item
		}
		call.result, call.failed = importFailureResult("Validazione fallita: "+dto.PythonRepr(items)), true
		return nil
	}
	if err := r.preflight(); err != nil {
		return err
	}
	tx, err := r.s.db.BeginTxx(r.ctx, nil)
	if err != nil {
		return err
	}
	call.tx = tx
	r.effects = &importeffects.Effects{Plan: r.s.plan, Tx: tx, Audit: r.s.audit, Now: r.s.now, NewUUID: r.s.ids}
	err = r.importAll()
	var python *consumerImportPyError
	if errors.As(err, &python) {
		_ = tx.Rollback()
		call.tx = nil
		call.result, call.failed = importFailureResult(python.message), true
		return nil
	}
	if err != nil {
		return err
	}
	email := sql.NullString{}
	if err := tx.GetContext(r.ctx, &email, `SELECT "email" FROM "bakney_user" WHERE "user_id" = $1::uuid`,
		r.association.Get("user_id").S); err != nil {
		return err
	}
	denomination, err := consumerImportJSONResult(r.association.Get("denomination"))
	if err != nil {
		return err
	}
	var ownerEmail any
	if email.Valid {
		ownerEmail = email.String
	}
	warnings := make([]any, len(r.warnings))
	for i, warning := range r.warnings {
		warnings[i] = warning
	}
	call.media = r.media
	call.result = celery.Dict{{Key: "success", Value: true}, {Key: "sport_association_id", Value: r.association.PK().S},
		{Key: "denomination", Value: denomination}, {Key: "owner_email", Value: ownerEmail}, {Key: "stats", Value: r.stats},
		{Key: "skipped_records", Value: r.skipped}, {Key: "warnings", Value: warnings}}
	return nil
}

func consumerImportJSONResult(value importeffects.Value) (any, error) {
	switch value.Kind {
	case importeffects.None:
		return nil, nil
	case importeffects.Str:
		return value.S, nil
	}
	return nil, errConsumerImportUnsupported
}

// validate is AssociationImportService.validate(): the error list, or an
// error for inputs handed off.
func (r *consumerImportRun) validate() ([]string, error) {
	isZip, err := consumerImportIsZipFile(r.path)
	if err != nil {
		return nil, err
	}
	if !isZip {
		return []string{"Not a valid ZIP file: " + r.path}, nil
	}
	archive, err := consumerImportOpenArchive(r.path)
	if err != nil {
		return nil, err
	}
	r.archive = archive
	if !archive.has("manifest.json") {
		return []string{"Missing manifest.json in export file"}, nil
	}
	data, err := archive.read("manifest.json")
	if err != nil {
		return nil, err
	}
	decoded, err := consumerImportJSON(data)
	if err != nil {
		return nil, err
	}
	manifest, ok := decoded.(domain.RequestObject)
	if !ok {
		return []string{"Error validating export file: '" + importTypeName(decoded) + "' object has no attribute 'get'"}, nil
	}
	skipped, err := r.skippedRecords(manifest)
	if err != nil {
		var python *consumerImportPyError
		if errors.As(err, &python) {
			return []string{"Error validating export file: " + python.message}, nil
		}
		return nil, err
	}
	r.skipped = skipped
	errorsList := []string{}
	format, _ := importGet(manifest, "export_format")
	if format != "bakney_sport_export_v1" {
		text, err := importStr(format)
		if err != nil {
			return nil, err
		}
		errorsList = append(errorsList, "Unsupported export format: "+text)
	}
	association, owner, err := r.loadIdentity()
	var python *consumerImportPyError
	if errors.As(err, &python) {
		if strings.HasPrefix(python.message, "\x00outer:") {
			return append(errorsList, "Error validating export file: "+strings.TrimPrefix(python.message, "\x00outer:")), nil
		}
		return append(errorsList, python.message), nil
	}
	if err != nil {
		return nil, err
	}
	associationID, _ := importGet(association, "sport_association_id")
	associationText, ok := associationID.(string)
	if !ok || !importeffects.CanonicalUUID(associationText) || !importeffects.CanonicalUUID(r.ownerUserID) {
		return nil, errConsumerImportUnsupported
	}
	var exists bool
	if err := r.s.db.GetContext(r.ctx, &exists, `SELECT EXISTS(SELECT 1 FROM "application_sportassociation" WHERE
 "sport_association_id" = $1::uuid)`, associationText); err != nil {
		return nil, err
	}
	if exists {
		errorsList = append(errorsList, "SportAssociation UUID collision: "+associationText+" already exists")
	}
	existing := sql.NullString{}
	found := true
	err = r.s.db.GetContext(r.ctx, &existing, `SELECT "password" FROM "bakney_user" WHERE "user_id" = $1::uuid`, r.ownerUserID)
	if errors.Is(err, sql.ErrNoRows) {
		found = false
	} else if err != nil {
		return nil, err
	}
	if found {
		conflict, err := r.conflictingAssociation(r.s.db, r.ownerUserID, associationText)
		if err != nil {
			return nil, err
		}
		if conflict != "" {
			errorsList = append(errorsList, "Owner user already owns another SportAssociation: "+conflict)
		}
	}
	archived, err := consumerImportRestorable(importGetValue(owner, "password"))
	if err != nil {
		return nil, err
	}
	available := archived
	if !available && found {
		if available, err = consumerImportRestorable(existing.String); err != nil {
			return nil, err
		}
	}
	if !available && r.password == "" {
		errorsList = append(errorsList, "Owner recovery password is required because the archive does not contain a supported "+
			"usable password hash")
	}
	return errorsList, nil
}

func importGetValue(object domain.RequestObject, key string) any {
	value, _ := importGet(object, key)
	return value
}

// consumerImportRestorable is is_restorable_password_hash; encodings the
// configured hashers would decide are unsupported.
func consumerImportRestorable(value any) (bool, error) {
	text, ok := value.(string)
	if !ok {
		return false, nil
	}
	restorable, supported := restorablePasswordHash(text)
	if !supported {
		return false, errConsumerImportUnsupported
	}
	return restorable, nil
}

func (r *consumerImportRun) conflictingAssociation(q sqlx.QueryerContext, owner, association string) (string, error) {
	var conflict string
	err := sqlx.GetContext(r.ctx, q, &conflict, `SELECT "sport_association_id"::text FROM "application_sportassociation"
 WHERE "user_id" = $1::uuid AND NOT ("sport_association_id" = $2::uuid) ORDER BY "sport_association_id" ASC LIMIT 1`, owner, association)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return conflict, err
}

// skippedRecords is legacy_backup.skipped_records(archive, manifest).
func (r *consumerImportRun) skippedRecords(manifest domain.RequestObject) (celery.Dict, error) {
	counts := celery.Dict{}
	specs, present := importGet(manifest, "models_exported")
	if !present {
		return counts, nil
	}
	list, ok := specs.([]any)
	if !ok {
		return nil, errConsumerImportUnsupported
	}
	for _, raw := range list {
		spec, ok := raw.(domain.RequestObject)
		if !ok {
			if _, container := raw.([]any); container {
				return nil, errConsumerImportUnsupported
			}
			return nil, importError("'" + importTypeName(raw) + "' object has no attribute 'get'")
		}
		nameValue, _ := importGet(spec, "name")
		name, isText := nameValue.(string)
		expected, deprecated := r.s.plan.DeprecatedSections[name]
		if !isText || !deprecated {
			switch nameValue.(type) {
			case []any, domain.RequestObject:
				return nil, errConsumerImportUnsupported
			}
			continue
		}
		file, _ := importGet(spec, "file")
		duplicate := false
		for _, item := range counts {
			duplicate = duplicate || item.Key == name
		}
		if duplicate || file != expected {
			return nil, importError("Duplicate or invalid retired section: " + name)
		}
		if !r.archive.has(expected) {
			return nil, importError("Missing retired section: " + expected)
		}
		data, err := r.archive.read(expected)
		if err != nil {
			return nil, err
		}
		decoded, err := consumerImportJSON(data)
		if err != nil {
			return nil, err
		}
		rows, ok := decoded.([]any)
		if !ok {
			return nil, errConsumerImportUnsupported
		}
		for _, row := range rows {
			if _, ok := row.(domain.RequestObject); !ok {
				return nil, importError("Invalid retired record: " + name)
			}
		}
		count, _ := importGet(spec, "count")
		if importTypeName(count) != "int" {
			return nil, importError("Invalid retired record count: " + name)
		}
		if parsed, err := importeffects.FromJSON(count); err != nil || parsed.S != strconv.Itoa(len(rows)) {
			return nil, importError("Invalid retired record count: " + name)
		}
		counts = append(counts, celery.Item{Key: name, Value: int64(len(rows))})
	}
	return counts, nil
}

// loadIdentity is _load_source_identity. Python errors raised outside its
// ValueError handling (AttributeError on a non-dict record) are marked so
// validate() reports them as validate()'s outer handler does.
func (r *consumerImportRun) loadIdentity() (domain.RequestObject, domain.RequestObject, error) {
	if r.associationData != nil && r.ownerData != nil {
		return r.associationData, r.ownerData, nil
	}
	if !r.archive.has("data/01_sport_association.json") {
		return nil, nil, importError("Missing required data file: 01_sport_association.json")
	}
	data, err := r.archive.read("data/01_sport_association.json")
	if err != nil {
		return nil, nil, err
	}
	decoded, err := consumerImportJSON(data)
	if err != nil {
		return nil, nil, err
	}
	list, ok := decoded.([]any)
	if !ok || len(list) != 1 {
		return nil, nil, importError("Export must contain exactly one SportAssociation record in data/01_sport_association.json")
	}
	association, ok := list[0].(domain.RequestObject)
	if !ok {
		return nil, nil, importError("\x00outer:'" + importTypeName(list[0]) + "' object has no attribute 'get'")
	}
	associationID, _ := importGet(association, "sport_association_id")
	if !importTruthy(associationID) {
		return nil, nil, importError("SportAssociation record is missing sport_association_id")
	}
	ownerID, _ := importGet(association, "user_id")
	if !importTruthy(ownerID) {
		return nil, nil, importError("SportAssociation record is missing user_id for owner selection")
	}
	ownerText, err := importStr(ownerID)
	if err != nil {
		return nil, nil, err
	}
	if !r.archive.has("data/02_users.json") {
		return nil, nil, importError("Missing required data file: 02_users.json")
	}
	users, err := r.records("data/02_users.json")
	if err != nil {
		return nil, nil, err
	}
	matches := []domain.RequestObject{}
	ids := map[string]bool{}
	for _, raw := range users {
		user, ok := raw.(domain.RequestObject)
		if !ok {
			return nil, nil, importError("\x00outer:'" + importTypeName(raw) + "' object has no attribute 'get'")
		}
		id, _ := importGet(user, "user_id")
		text, err := importStr(id)
		if err != nil {
			return nil, nil, err
		}
		if id != nil {
			ids[text] = true
		}
		if text == ownerText {
			matches = append(matches, user)
		}
	}
	if len(matches) != 1 {
		return nil, nil, importError("Expected exactly one User with user_id " + ownerText + " matching SportAssociation.user_id")
	}
	associationText, err := importStr(associationID)
	if err != nil {
		return nil, nil, err
	}
	r.associationData, r.ownerData = association, matches[0]
	r.associationID, r.ownerUserID = associationText, ownerText
	r.sourcePKs = map[string]map[string]bool{"SportAssociation": {associationText: true}, "User": ids}
	return association, matches[0], nil
}

// preflight reads every data file and media member before the transaction
// starts, so content the port cannot read is handed off before any write.
func (r *consumerImportRun) preflight() error {
	r.parsed = map[string][]any{}
	// Records of an audited model whose __str__ is not reproduced are handed
	// off here, before any write consumes sequence values.
	files := map[string][]string{}
	for _, entry := range r.s.plan.Order {
		files[entry.Model] = append(files[entry.Model], "data/"+entry.File)
	}
	if data, err := r.archive.read("manifest.json"); err == nil {
		if decoded, err := consumerImportJSON(data); err == nil {
			if manifest, ok := decoded.(domain.RequestObject); ok {
				specs, _ := importGet(manifest, "models_exported")
				list, _ := specs.([]any)
				for _, raw := range list {
					spec, ok := raw.(domain.RequestObject)
					if !ok {
						continue
					}
					name, _ := importGet(spec, "name")
					file, _ := importGet(spec, "file")
					nameText, ok1 := name.(string)
					fileText, ok2 := file.(string)
					if ok1 && ok2 {
						files[nameText] = append(files[nameText], "data/"+fileText[strings.LastIndex(fileText, "/")+1:])
					}
				}
			}
		}
	}
	for _, model := range r.s.plan.Models {
		if model.Audit == nil || model.Str == "default" || model.Str == "user" {
			continue
		}
		for _, name := range files[model.Name] {
			if !r.archive.has(name) {
				continue
			}
			records, err := r.records(name)
			if err != nil {
				return err
			}
			if len(records) > 0 {
				return errConsumerImportUnsupported
			}
		}
	}
	for _, name := range r.archive.names {
		switch {
		case strings.HasPrefix(name, "data/") && strings.HasSuffix(name, ".json"):
			if _, err := r.records(name); err != nil {
				return err
			}
		case strings.HasPrefix(name, "files/") && !strings.HasSuffix(name, "/"):
			if _, err := r.archive.verify(name); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r *consumerImportRun) records(name string) ([]any, error) {
	if list, ok := r.parsed[name]; ok {
		return list, nil
	}
	data, err := r.archive.read(name)
	if err != nil {
		return nil, err
	}
	decoded, err := consumerImportJSON(data)
	if err != nil {
		return nil, err
	}
	list, ok := decoded.([]any)
	if !ok {
		return nil, errConsumerImportUnsupported
	}
	if r.parsed != nil {
		r.parsed[name] = list
	}
	return list, nil
}

func (r *consumerImportRun) generateUUID(old string) string {
	r.uuidMapping[old] = old
	return old
}

func (r *consumerImportRun) register(model, oldPK string, instance *importeffects.Instance) {
	if r.importedObjects[model] == nil {
		r.importedObjects[model] = map[string]*importeffects.Instance{}
	}
	r.importedObjects[model][oldPK] = instance
	if model == "Document" {
		r.documentMapping[oldPK] = instance
	}
}

// importAll is import_all() without its media uploads (prepared in r.media).
func (r *consumerImportRun) importAll() error {
	ctx := r.ctx
	r.uuidMapping = map[string]string{}
	r.importedObjects = map[string]map[string]*importeffects.Instance{}
	r.importedModels = map[string]bool{}
	r.reusedUserPKs = map[string]bool{}
	r.documentMapping = map[string]*importeffects.Instance{}
	r.modelCache = map[string]bool{}
	data, err := r.archive.read("manifest.json")
	if err != nil {
		return err
	}
	decoded, err := consumerImportJSON(data)
	if err != nil {
		return err
	}
	manifest := decoded.(domain.RequestObject)
	if r.skipped, err = r.skippedRecords(manifest); err != nil {
		return err
	}
	associationInfo, present := importGet(manifest, "association")
	if !present {
		return importError("'association'")
	}
	info, ok := associationInfo.(domain.RequestObject)
	if !ok {
		return errConsumerImportUnsupported
	}
	if _, present := importGet(info, "denomination"); !present {
		return importError("'denomination'")
	}
	files := map[string]string{}
	if specs, present := importGet(manifest, "models_exported"); present {
		list, _ := specs.([]any)
		for _, raw := range list {
			spec := raw.(domain.RequestObject)
			name, _ := importGet(spec, "name")
			file, _ := importGet(spec, "file")
			if !importTruthy(name) || !importTruthy(file) {
				continue
			}
			nameText, ok1 := name.(string)
			fileText, ok2 := file.(string)
			if !ok1 || !ok2 {
				return errConsumerImportUnsupported
			}
			if strings.Contains(fileText, "/") {
				fileText = fileText[strings.LastIndex(fileText, "/")+1:]
			}
			files[nameText] = fileText
		}
	}
	if err := r.createOwnerUser(); err != nil {
		return err
	}
	if err := r.importSportAssociation(); err != nil {
		return err
	}
	for _, entry := range r.s.plan.Order {
		if entry.Model == "SportAssociation" {
			continue
		}
		filename := files[entry.Model]
		if filename == "" {
			for _, candidate := range r.s.plan.Order {
				if candidate.Model == entry.Model {
					filename = candidate.File
					break
				}
			}
		}
		if err := r.importModelData(filename, r.s.plan.Model(entry.Model)); err != nil {
			return err
		}
	}
	if err := r.resolveDeferredFKs(); err != nil {
		return err
	}
	if err := r.importM2M(); err != nil {
		return err
	}
	if err := r.effects.CheckConstraints(ctx); err != nil {
		return err
	}
	return r.importFiles()
}

func (r *consumerImportRun) loadUser(id string) (*importeffects.Instance, string, bool, error) {
	var row struct {
		Password  sql.NullString `db:"password"`
		FirstName sql.NullString `db:"first_name"`
		LastName  sql.NullString `db:"last_name"`
	}
	err := r.effects.Tx.GetContext(r.ctx, &row, `SELECT "password", "first_name", "last_name" FROM "bakney_user" WHERE "user_id" = $1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, "", false, nil
	}
	if err != nil {
		return nil, "", false, err
	}
	model := r.s.plan.Model("User")
	instance := &importeffects.Instance{Model: model, Values: map[string]importeffects.Value{"user_id": importeffects.UUIDValue(id)}}
	return instance, row.Password.String, true, nil
}

// ownerPasswordHash is _owner_password_hash.
func (r *consumerImportRun) ownerPasswordHash(owner domain.RequestObject) (string, error) {
	archived := importGetValue(owner, "password")
	restorable, err := consumerImportRestorable(archived)
	if err != nil {
		return "", err
	}
	if restorable {
		return archived.(string), nil
	}
	if r.password != "" {
		return importMakePassword(r.password)
	}
	return "", importError("Owner recovery password is required because the archive does not contain a supported usable password hash")
}

const importRandomCharacters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

func importRandomString(length int) (string, error) {
	out := make([]byte, length)
	for i := range out {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(importRandomCharacters))))
		if err != nil {
			return "", err
		}
		out[i] = importRandomCharacters[n.Int64()]
	}
	return string(out), nil
}

// importMakePassword is make_password(password) with Django 5.2's default
// PBKDF2PasswordHasher (1,000,000 iterations, a 22-character salt).
func importMakePassword(password string) (string, error) {
	salt, err := importRandomString(22)
	if err != nil {
		return "", err
	}
	digest := pbkdf2.Key([]byte(password), []byte(salt), 1000000, sha256.Size, sha256.New)
	return "pbkdf2_sha256$1000000$" + salt + "$" + base64.StdEncoding.EncodeToString(digest), nil
}

// importUnusablePassword is make_password(None).
func importUnusablePassword() (string, error) {
	suffix, err := importRandomString(40)
	return "!" + suffix, err
}

func (r *consumerImportRun) createOwnerUser() error {
	association, owner, err := r.loadIdentity()
	if err != nil {
		return err
	}
	old, err := importStr(importGetValue(owner, "user_id"))
	if err != nil {
		return err
	}
	id := r.generateUUID(old)
	if !importeffects.CanonicalUUID(id) {
		return errConsumerImportUnsupported
	}
	existing, password, found, err := r.loadUser(id)
	if err != nil {
		return err
	}
	var user *importeffects.Instance
	if found {
		associationID, err := importStr(importGetValue(association, "sport_association_id"))
		if err != nil || !importeffects.CanonicalUUID(associationID) {
			return errConsumerImportUnsupported
		}
		conflict, err := r.conflictingAssociation(r.effects.Tx, id, associationID)
		if err != nil {
			return err
		}
		if conflict != "" {
			return importError("Owner user " + id + " already owns another SportAssociation (" + conflict + "); cannot import a second " +
				"association for the same owner user")
		}
		restorable, err := consumerImportRestorable(password)
		if err != nil {
			return err
		}
		if !restorable {
			hash, err := r.ownerPasswordHash(owner)
			if err != nil {
				return err
			}
			if err := r.effects.UpdatePassword(r.ctx, r.s.plan.Model("User"), id, hash); err != nil {
				return err
			}
		}
		user = existing
		r.reusedUserPKs[old] = true
	} else {
		data, err := r.sanitizeUser(append(domain.RequestObject(nil), owner...), true)
		if err != nil {
			return err
		}
		data = importSet(data, "user_id", id)
		user, err = r.buildInstance(r.s.plan.Model("User"), data)
		if err != nil {
			return err
		}
		if err := r.effects.Save(r.ctx, user); err != nil {
			return err
		}
	}
	r.ownerUser = user
	r.register("User", old, user)
	r.importedModels["User"] = true
	r.setStat("User", 1)
	return nil
}

// sanitizeUser is _sanitize_user_data.
func (r *consumerImportRun) sanitizeUser(data domain.RequestObject, owner bool) (domain.RequestObject, error) {
	var password string
	var err error
	if owner {
		password, err = r.ownerPasswordHash(data)
	} else {
		archived := importGetValue(data, "password")
		restorable, rerr := consumerImportRestorable(archived)
		if rerr != nil {
			return nil, rerr
		}
		if restorable {
			password = archived.(string)
		} else {
			password, err = importUnusablePassword()
		}
	}
	if err != nil {
		return nil, err
	}
	data = importSet(data, "password", password)
	for _, item := range []domain.RequestField{{Name: "is_staff", Value: false}, {Name: "is_superuser", Value: false},
		{Name: "two_fa", Value: false}, {Name: "two_fa_secret", Value: nil}, {Name: "stripe_account_id", Value: nil},
		{Name: "stripe_on_boarding_completed", Value: false}, {Name: "integration_google_state", Value: nil},
		{Name: "integration_google_credentials", Value: nil}, {Name: "integration_google_auto_sync", Value: false}} {
		data = importSet(data, item.Name, item.Value)
	}
	if owner {
		data = importSet(data, "role", importJSONInt(1))
		data = importSet(data, "is_active", true)
	}
	return data, nil
}

func importJSONInt(n int) any { return json.Number(strconv.Itoa(n)) }

// buildInstance is model(**_build_model_kwargs(model, data)).
func (r *consumerImportRun) buildInstance(model *importeffects.Model, data domain.RequestObject) (*importeffects.Instance, error) {
	kwargs := map[string]importeffects.Value{}
	for _, field := range model.Fields {
		if field.IsFK() {
			raw, present := importGet(data, field.Attname)
			if !present {
				continue
			}
			var value importeffects.Value
			var err error
			switch v := raw.(type) {
			case importeffects.Value:
				value = v
			default:
				value, err = importeffects.ParseRelated(field, v)
			}
			if err != nil {
				return nil, err
			}
			kwargs[field.Attname] = value
			continue
		}
		raw, present := importGet(data, field.Name)
		if !present {
			continue
		}
		var value importeffects.Value
		var err error
		if v, ok := raw.(importeffects.Value); ok {
			value = v
		} else {
			value, err = importeffects.ParseArchived(field, raw)
		}
		if err != nil {
			return nil, err
		}
		kwargs[field.Attname] = value
	}
	return r.effects.NewInstance(model, kwargs)
}

func (r *consumerImportRun) importSportAssociation() error {
	data, _, err := r.loadIdentity()
	if err != nil {
		return err
	}
	old, err := importStr(importGetValue(data, "sport_association_id"))
	if err != nil {
		return err
	}
	id := r.generateUUID(old)
	if !importeffects.CanonicalUUID(id) {
		return errConsumerImportUnsupported
	}
	model := r.s.plan.Model("SportAssociation")
	exists, err := r.effects.Exists(r.ctx, model, importeffects.UUIDValue(id))
	if err != nil {
		return err
	}
	if exists {
		return importError("SportAssociation UUID collision: " + id + " already exists")
	}
	owner := r.ownerUser.PK().S
	conflict, err := r.conflictingAssociation(r.effects.Tx, owner, id)
	if err != nil {
		return err
	}
	if conflict != "" {
		return importError("Owner user " + owner + " already owns another SportAssociation (" + conflict + "); cannot import a second " +
			"association for the same owner user")
	}
	values := append(domain.RequestObject(nil), data...)
	values = importSet(values, "sport_association_id", id)
	values = importSet(values, "user_id", owner)
	association, err := r.buildInstance(model, values)
	if err != nil {
		return err
	}
	if err := r.effects.Save(r.ctx, association); err != nil {
		return err
	}
	r.association = association
	r.register("SportAssociation", old, association)
	r.importedModels["SportAssociation"] = true
	r.setStat("SportAssociation", 1)
	return nil
}

func (r *consumerImportRun) importModelData(filename string, model *importeffects.Model) error {
	path := "data/" + filename
	if !r.archive.has(path) {
		return nil
	}
	records, err := r.records(path)
	if err != nil {
		return err
	}
	pkField := r.s.plan.PKFields[model.Name]
	if model.Name != "Folder" && pkField != "" && r.sourcePKs[model.Name] == nil && model.SelfReference {
		keys := map[string]bool{}
		for _, raw := range records {
			record, ok := raw.(domain.RequestObject)
			if !ok {
				continue
			}
			if value, _ := importGet(record, pkField); value != nil {
				text, err := importStr(value)
				if err != nil {
					return err
				}
				keys[text] = true
			}
		}
		r.sourcePKs[model.Name] = keys
	}
	if model.Name == "Folder" {
		return r.importFolders(records)
	}
	count := int64(0)
	for _, raw := range records {
		record, ok := raw.(domain.RequestObject)
		if !ok {
			return errConsumerImportUnsupported
		}
		created, err := r.importRecord(model, pkField, record)
		if err != nil {
			return err
		}
		if created {
			count++
		}
	}
	r.setStat(model.Name, count)
	r.importedModels[model.Name] = true
	return nil
}

func (r *consumerImportRun) importRecord(model *importeffects.Model, pkField string, record domain.RequestObject) (bool, error) {
	var oldPK string
	hasOld := false
	if pkField != "" {
		if value, _ := importGet(record, pkField); value != nil {
			text, err := importStr(value)
			if err != nil {
				return false, err
			}
			oldPK, hasOld = text, true
		}
	}
	instance, err := r.createModelInstance(model, append(domain.RequestObject(nil), record...))
	if err != nil {
		return false, err
	}
	if instance == nil {
		return false, nil
	}
	if instance.Adding {
		switch model.Name {
		case "Message", "MessageTransaction":
			if err := r.saveImportedMessage(instance, record); err != nil {
				return false, err
			}
		default:
			if err := r.effects.Save(r.ctx, instance); err != nil {
				return false, err
			}
		}
	}
	if hasOld {
		r.register(model.Name, oldPK, instance)
	}
	return true, nil
}

// saveImportedMessage is _save_imported_message.
func (r *consumerImportRun) saveImportedMessage(instance *importeffects.Instance, record domain.RequestObject) error {
	field := "sent_on"
	if instance.Model.Name == "Message" {
		field = "created_at"
	}
	raw, present := importGet(record, field)
	if !present {
		return r.effects.Save(r.ctx, instance)
	}
	if raw != nil && instance.Get(field).IsNone() {
		return importError("Invalid imported message timestamp")
	}
	return r.effects.RawSave(r.ctx, instance, instance.Model.Name == "Message" && instance.Model.Audit != nil)
}

// createModelInstance is _create_model_instance (nil for the skipped owner).
func (r *consumerImportRun) createModelInstance(model *importeffects.Model, data domain.RequestObject) (*importeffects.Instance, error) {
	name := model.Name
	r.modelCache[name] = true
	pkField := r.s.plan.PKFields[name]
	var oldPK any
	hasOld := false
	if pkField != "" {
		if value, present := importGet(data, pkField); present {
			oldPK, hasOld = value, true
			text, err := importStr(value)
			if err != nil {
				return nil, err
			}
			data = importSet(data, pkField, r.generateUUID(text))
		}
	}
	oldText := ""
	if hasOld {
		oldText, _ = importStr(oldPK)
	}
	if name == "User" {
		data, _, _ = importPop(data, "_m2m_preview_and_custom_features")
		if hasOld && oldPK != nil && oldText == r.ownerUserID {
			return nil, nil
		}
		id, _ := importGet(data, pkField)
		idText, ok := id.(string)
		if !ok || !importeffects.CanonicalUUID(idText) {
			return nil, errConsumerImportUnsupported
		}
		existing, password, found, err := r.loadUser(idText)
		if err != nil {
			return nil, err
		}
		if found {
			current, err := consumerImportRestorable(password)
			if err != nil {
				return nil, err
			}
			archived := importGetValue(data, "password")
			restorable, err := consumerImportRestorable(archived)
			if err != nil {
				return nil, err
			}
			if !current && restorable {
				if err := r.effects.UpdatePassword(r.ctx, model, idText, archived.(string)); err != nil {
					return nil, err
				}
			}
			r.reusedUserPKs[oldText] = true
			return existing, nil
		}
	} else if pkField != "" {
		if value, _ := importGet(data, pkField); value != nil {
			text, ok := value.(string)
			if !ok || !importeffects.CanonicalUUID(text) || model.PK.Kind != "uuid" || model.PK.Name != pkField {
				return nil, errConsumerImportUnsupported
			}
			exists, err := r.effects.Exists(r.ctx, model, importeffects.UUIDValue(text))
			if err != nil {
				return nil, err
			}
			if exists {
				return nil, importError(name + " UUID collision: " + text + " already exists. Only User rows may be reused by exact UUID during import.")
			}
		}
	}
	if !hasOld && model.PK.Kind == "uuid" {
		// A missing key would take uuid4()'s random value.
		return nil, errConsumerImportUnsupported
	}
	// Deferred many-to-many values.
	m2m := []domain.RequestField{}
	kept := domain.RequestObject{}
	for _, item := range data {
		if strings.HasPrefix(item.Name, "_m2m_") {
			m2m = append(m2m, domain.RequestField{Name: item.Name[5:], Value: item.Value})
			continue
		}
		kept = append(kept, item)
	}
	data = kept
	if len(m2m) > 0 && hasOld && importTruthy(oldPK) {
		for _, item := range m2m {
			if !importTruthy(item.Value) {
				continue
			}
			list, ok := item.Value.([]any)
			if !ok || model.M2MField(item.Name) == nil {
				return nil, errConsumerImportUnsupported
			}
			r.deferredM2M = append(r.deferredM2M, consumerImportDeferredM2M{model: name, pk: oldText, field: item.Name, related: list})
		}
	}
	// Foreign keys: FK_MAPPINGS, then every many-to-one field to a PK_FIELDS model.
	mappings := domain.RequestObject{}
	for _, pair := range r.s.plan.FKMappings[name] {
		mappings = importSet(mappings, pair[0], pair[1])
	}
	for _, field := range model.Fields {
		if field.ManyToOne() {
			if _, mapped := r.s.plan.PKFields[field.TargetModel]; mapped {
				mappings = importSet(mappings, field.Attname, field.TargetModel)
			}
		}
	}
	for _, mapping := range mappings {
		fkField, related := mapping.Name, mapping.Value.(string)
		value, present := importGet(data, fkField)
		if !present || !importTruthy(value) {
			continue
		}
		oldFK, err := importStr(value)
		if err != nil {
			return nil, err
		}
		pendingSelf := related == name && r.sourcePKs[related][oldFK] && r.importedObjects[related][oldFK] == nil
		if !r.importedModels[related] || pendingSelf {
			if hasOld && importTruthy(oldPK) {
				r.deferredFKs = append(r.deferredFKs, consumerImportDeferred{model: name, pk: oldText, field: fkField, target: oldFK})
			}
			data = importSet(data, fkField, nil)
			continue
		}
		resolved := r.resolveFK(oldFK, related)
		if resolved == nil {
			r.warnings = append(r.warnings, name+" references missing "+related+" "+oldFK)
		}
		data = importSet(data, fkField, resolved)
	}
	if name == "User" {
		var err error
		if data, err = r.sanitizeUser(data, false); err != nil {
			return nil, err
		}
	}
	if name == "Associate" {
		data = importSet(data, "sport_association_id", r.association.PK().S)
	}
	return r.buildInstance(model, data)
}

// resolveFK is _resolve_fk: the imported object's key, else the UUID mapping.
func (r *consumerImportRun) resolveFK(old, related string) any {
	if instance := r.importedObjects[related][old]; instance != nil && !instance.PK().IsNone() {
		return instance.PK()
	}
	if mapped, ok := r.uuidMapping[old]; ok {
		return mapped
	}
	return nil
}

// importFolders is _import_folders.
func (r *consumerImportRun) importFolders(records []any) error {
	type pending struct {
		key    string
		record domain.RequestObject
	}
	remaining := []pending{}
	seen := map[string]bool{}
	for _, raw := range records {
		record, ok := raw.(domain.RequestObject)
		if !ok {
			return errConsumerImportUnsupported
		}
		key, err := importStr(importGetValue(record, "id"))
		if err != nil {
			return err
		}
		if key == "None" {
			return importError("Folder record is missing its id")
		}
		if seen[key] {
			return importError("Duplicate Folder id: " + key)
		}
		seen[key] = true
		remaining = append(remaining, pending{key: key, record: record})
	}
	r.sourcePKs["Folder"] = seen
	if r.importedObjects["Folder"] == nil {
		r.importedObjects["Folder"] = map[string]*importeffects.Instance{}
	}
	imported := r.importedObjects["Folder"]
	model := r.s.plan.Model("Folder")
	count := int64(0)
	for len(remaining) > 0 {
		progressed := false
		next := []pending{}
		for _, item := range remaining {
			parentID, _ := importGet(item.record, "parent_id")
			parent := importeffects.NoneValue
			if parentID != nil {
				text, err := importStr(parentID)
				if err != nil {
					return err
				}
				instance := imported[text]
				if instance == nil {
					next = append(next, item)
					continue
				}
				parent = instance.PK()
			}
			name, present := importGet(item.record, "name")
			if !present {
				name = ""
			}
			nameValue, err := importeffects.ParseArchived(model.Field("name"), name)
			if err != nil || nameValue.Kind != importeffects.Str {
				return errConsumerImportUnsupported
			}
			kwargs := map[string]importeffects.Value{"name": nameValue, "parent_id": parent,
				"sport_association_id": r.association.PK()}
			if created, _ := importGet(item.record, "created_at"); importTruthy(created) {
				text, ok := created.(string)
				if !ok {
					return errConsumerImportUnsupported
				}
				moment, err := importeffects.ParseDateTime(text)
				if err != nil {
					return err
				}
				kwargs["created_at"] = importeffects.DateTimeValue(moment)
			}
			folder, err := r.effects.NewInstance(model, kwargs)
			if err != nil {
				return err
			}
			if err := r.effects.SaveTreeNode(r.ctx, folder); err != nil {
				return err
			}
			count++
			imported[item.key] = folder
			progressed = true
		}
		// Items left in this pass keep their relative order for the next one.
		remaining = next
		if !progressed {
			keys := make([]string, len(remaining))
			for i, item := range remaining {
				keys[i] = item.key
			}
			sort.Strings(keys)
			return importError("Folder hierarchy has missing or circular parents: " + strings.Join(keys, ", "))
		}
	}
	r.setStat("Folder", count)
	r.importedModels["Folder"] = true
	return nil
}

// resolveDeferredFKs is _resolve_deferred_fks (pass 2).
func (r *consumerImportRun) resolveDeferredFKs() error {
	resolved, failed := int64(0), int64(0)
	for _, model := range r.deferredOrder(func(i int) string { return r.deferredFKs[i].model }, len(r.deferredFKs)) {
		plan := r.s.plan.Model(model)
		if !r.modelCache[model] || r.s.plan.PKFields[model] == "" {
			continue
		}
		for _, item := range r.deferredFKs {
			if item.model != model {
				continue
			}
			if model == "User" && r.reusedUserPKs[item.pk] {
				continue
			}
			pk, ok := r.uuidMapping[item.pk]
			if !ok || pk == "" {
				failed++
				continue
			}
			target, ok := r.uuidMapping[item.target]
			if !ok || target == "" {
				failed++
				continue
			}
			field := plan.FieldByAttname(item.field)
			if field == nil || plan.PK.Name != r.s.plan.PKFields[model] || !importeffects.CanonicalUUID(pk) {
				return errConsumerImportUnsupported
			}
			value, err := importeffects.ParseRelated(field, target)
			if err != nil {
				return err
			}
			if _, err := r.effects.QuerysetUpdate(r.ctx, plan, importeffects.UUIDValue(pk), item.field, value); err != nil {
				return err
			}
			resolved++
		}
	}
	r.setStat("deferred_fks_resolved", resolved)
	r.setStat("deferred_fks_failed", failed)
	return nil
}

// deferredOrder lists model names in first-appearance order (the dict of lists).
func (r *consumerImportRun) deferredOrder(model func(int) string, count int) []string {
	seen := map[string]bool{}
	order := []string{}
	for i := 0; i < count; i++ {
		if name := model(i); !seen[name] {
			seen[name] = true
			order = append(order, name)
		}
	}
	return order
}

// importM2M is _import_m2m_relationships (pass 3).
func (r *consumerImportRun) importM2M() error {
	resolved, failed := int64(0), int64(0)
	for _, model := range r.deferredOrder(func(i int) string { return r.deferredM2M[i].model }, len(r.deferredM2M)) {
		plan := r.s.plan.Model(model)
		if !r.modelCache[model] || r.s.plan.PKFields[model] == "" {
			continue
		}
		for _, item := range r.deferredM2M {
			if item.model != model {
				continue
			}
			if model == "User" && r.reusedUserPKs[item.pk] {
				continue
			}
			pk, ok := r.uuidMapping[item.pk]
			if !ok || pk == "" {
				failed++
				continue
			}
			if plan.PK.Name != r.s.plan.PKFields[model] || !importeffects.CanonicalUUID(pk) {
				return errConsumerImportUnsupported
			}
			exists, err := r.effects.Exists(r.ctx, plan, importeffects.UUIDValue(pk))
			if err != nil {
				return err
			}
			if !exists {
				failed++
				continue
			}
			targets := []string{}
			for _, raw := range item.related {
				text, err := importStr(raw)
				if err != nil {
					return err
				}
				if mapped := r.uuidMapping[text]; mapped != "" {
					targets = append(targets, mapped)
				}
			}
			if len(targets) > 0 {
				if err := r.effects.SetM2M(r.ctx, plan, plan.M2MField(item.field), importeffects.UUIDValue(pk), targets); err != nil {
					return err
				}
			}
			resolved++
		}
	}
	r.setStat("m2m_resolved", resolved)
	r.setStat("m2m_failed", failed)
	return nil
}

// importFiles is _import_files (pass 4): the database part runs here, the
// uploads are listed for Run with the names default_storage.save returns.
func (r *consumerImportRun) importFiles() error {
	count := int64(0)
	for _, name := range r.archive.names {
		if !strings.HasPrefix(name, "files/") || strings.HasSuffix(name, "/") {
			continue
		}
		parts := strings.Split(name[len("files/"):], "/")
		if len(parts) < 3 {
			continue
		}
		category, old, filename := parts[0], parts[1], parts[2]
		var err error
		if category == "subscription_signatures" || category == "signatures" {
			err = r.importSignatureFile(name, old, filename)
		} else {
			err = r.importDocumentFile(name, old, filename)
		}
		if err != nil {
			return err
		}
		count++
	}
	r.setStat("files_imported", count)
	return nil
}

// saveName is default_storage.save(path, ...)'s returned name and the
// upload it implies; an existing object (get_available_name would pick a
// random alternative) is unsupported.
func (r *consumerImportRun) saveName(member, path string) (string, error) {
	cleaned, err := documentkey.SaveName(path)
	if err != nil {
		return "", errConsumerImportUnsupported
	}
	key, err := documentkey.S3Key(cleaned, r.s.objects.Location())
	if err != nil {
		return "", errConsumerImportUnsupported
	}
	if !r.s.settings.Overwrite {
		exists, err := r.s.objects.Exists(r.ctx, key)
		if err != nil || exists {
			return "", errConsumerImportUnsupported
		}
	}
	for _, media := range r.media {
		if media.key == key {
			return "", errConsumerImportUnsupported
		}
	}
	kind, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return "", errConsumerImportUnsupported
	}
	if !known {
		kind = "application/octet-stream"
	}
	r.media = append(r.media, consumerImportMedia{member: member, key: key, contentType: kind, encoding: encoding})
	return cleaned, nil
}

func (r *consumerImportRun) timestamp() string { return r.s.now().UTC().Format("20060102_150405") }

func (r *consumerImportRun) importDocumentFile(member, old, filename string) error {
	model := r.s.plan.Model("Document")
	document := r.documentMapping[old]
	if document == nil {
		id := r.generateUUID(old)
		document = r.importedObjects["Document"][old]
		if document == nil {
			if !importeffects.CanonicalUUID(id) {
				return errConsumerImportUnsupported
			}
			exists, err := r.effects.Exists(r.ctx, model, importeffects.UUIDValue(id))
			if err != nil {
				return err
			}
			if exists {
				return importError("Document UUID collision: " + id + " already exists. Only User rows may be reused by exact UUID during import.")
			}
			document, err = r.effects.NewInstance(model, map[string]importeffects.Value{"document_id": importeffects.UUIDValue(id),
				"filename": importeffects.StrValue(filename)})
			if err != nil {
				return err
			}
			if err := r.effects.Save(r.ctx, document); err != nil {
				return err
			}
			r.register("Document", old, document)
		}
		r.documentMapping[old] = document
	}
	saved, err := r.saveName(member, r.timestamp()+"/"+document.PK().S+"/"+filename)
	if err != nil {
		return err
	}
	document.Values["filepath"] = importeffects.StrValue(saved)
	return r.effects.SaveFields(r.ctx, document, "filepath")
}

func (r *consumerImportRun) importSignatureFile(member, old, filename string) error {
	id := r.uuidMapping[old]
	if id == "" {
		return importError("Signature media references missing Subscription " + old)
	}
	subscription := r.importedObjects["Subscription"][old]
	if subscription == nil {
		if !importeffects.CanonicalUUID(id) {
			return errConsumerImportUnsupported
		}
		exists, err := r.effects.Exists(r.ctx, r.s.plan.Model("Subscription"), importeffects.UUIDValue(id))
		if err != nil {
			return err
		}
		if exists {
			return errConsumerImportUnsupported
		}
		return importError("Signature media references missing Subscription " + old)
	}
	safe := filename
	if safe == "" {
		safe = "signature.png"
	}
	saved, err := r.saveName(member, "subscriptions/"+id+"/imported_signature_"+r.timestamp()+"_"+safe)
	if err != nil {
		return err
	}
	subscription.Values["signature_storage_key"] = importeffects.StrValue(saved)
	subscription.Values["signature_url"] = r.signaturePublicURL(saved)
	return r.effects.SaveFields(r.ctx, subscription, "signature_storage_key", "signature_url")
}

// signaturePublicURL is Subscription._signature_public_url.
func (r *consumerImportRun) signaturePublicURL(saved string) importeffects.Value {
	base := strings.TrimRight(strings.TrimSpace(r.s.settings.PublicBaseURL), "/")
	if base == "" {
		return importeffects.NoneValue
	}
	location := strings.Trim(r.s.objects.Location(), "/")
	key := strings.TrimLeft(saved, "/")
	if location != "" && !strings.HasPrefix(key, location+"/") {
		key = strings.TrimRight(location, "/") + "/" + key
	}
	return importeffects.StrValue(base + "/" + strings.TrimLeft(key, "/"))
}
