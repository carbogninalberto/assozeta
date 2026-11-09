package service

import (
	"context"
	"encoding/base64"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzip"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// ConsumerExportAssociationTask is application.tasks.export_association_data's Celery name.
const ConsumerExportAssociationTask = "export_association_data"

// Constants of BE/application/tasks.py and export_progress.py.
const (
	consumerRestoreExportMaxAgeDays     = 30
	consumerRestoreExportActiveTimeout  = 2 * time.Hour
	consumerRestoreExportLastTimeout    = 24 * time.Hour
	consumerRestoreExportThrottle       = time.Second
	consumerRestoreExportArchiveMaxSize = 1 << 30
)

// ConsumerExportStore is the database side of the export.
type ConsumerExportStore interface {
	ExportUser(ctx context.Context, id string) (repository.ExportUser, error)
	ExportAssociation(ctx context.Context, id string) (repository.ExportAssociation, error)
	DocumentReferences(ctx context.Context, id string) (int64, error)
	DeleteArchive(ctx context.Context, id, association string, at time.Time) error
	DeleteDocument(ctx context.Context, id string, at time.Time) error
	CreateDocument(ctx context.Context, document domain.NewDocument, at time.Time) error
	SetDocumentFile(ctx context.Context, id, path string, size int64, at time.Time) error
	CreateArchive(ctx context.Context, row domain.ArchiveRecord, created, at time.Time) error
}

// ConsumerExportCache is Django's default cache.
type ConsumerExportCache interface {
	Set(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) error
	Delete(ctx context.Context, key string) error
	TaskOf(ctx context.Context, key string) (taskID any, present, isDict bool, err error)
}

// ConsumerExportObjects is the S3 default storage.
type ConsumerExportObjects interface {
	ConsumerStorageObjects
	Delete(ctx context.Context, key string) error
	Missing(err error) bool
}

// ConsumerExportResults stores update_state(PROGRESS) rows.
type ConsumerExportResults interface {
	StoreResult(ctx context.Context, taskID, status, result, meta string) error
}

// ConsumerExportGroups is the channel layer.
type ConsumerExportGroups interface {
	GroupSend(ctx context.Context, group string, message channels.Dict) error
}

// ConsumerExportDatabase opens the connection the model serialization reads
// through (text results, simple protocol), closed by the returned function.
type ConsumerExportDatabase func(ctx context.Context) (associationexport.Conn, func(), error)

// ConsumerExportSettings are the core.settings values the task reads.
type ConsumerExportSettings struct {
	WhitelabelName, DefaultFromEmail string
	TempDir                          string
}

// ConsumerExportAssociation runs export_association_data(sport_association_id,
// user_id, include_files=True) and AssociationExportService.export().
type ConsumerExportAssociation struct {
	plan      *associationexport.Plan
	store     ConsumerExportStore
	cache     ConsumerExportCache
	objects   ConsumerExportObjects
	results   ConsumerExportResults
	groups    ConsumerExportGroups
	producer  celery.Publisher
	database  ConsumerExportDatabase
	settings  ConsumerExportSettings
	now       func() time.Time
	ids       func() string
	overwrite bool
}

func NewConsumerExportAssociation(store ConsumerExportStore, cache ConsumerExportCache, objects ConsumerExportObjects,
	results ConsumerExportResults, groups ConsumerExportGroups, producer celery.Publisher, database ConsumerExportDatabase,
	settings ConsumerExportSettings, overwrite bool, now func() time.Time, ids func() string) (*ConsumerExportAssociation, error) {
	plan, err := associationexport.LoadPlan()
	if err != nil {
		return nil, err
	}
	return &ConsumerExportAssociation{plan: plan, store: store, cache: cache, objects: objects, results: results, groups: groups,
		producer: producer, database: database, settings: settings, overwrite: overwrite, now: now, ids: ids}, nil
}

func (s *ConsumerExportAssociation) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.plan_, Run: s.run, Cleanup: s.release}
}

// release removes the temporary directory and archive Plan prepared.
func (s *ConsumerExportAssociation) release(request *celeryworker.Request) {
	call, _ := request.State.(*consumerRestoreExportCall)
	if call != nil && call.prepared != nil {
		call.prepared.remove()
	}
}

var consumerRestoreExportUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type consumerRestoreExportCall struct {
	association, user string
	associationValid  bool
	credentials       map[string]bool
	passwords         map[string]bool
	userRow           repository.ExportUser
	associationRow    repository.ExportAssociation
	// failure is str(e) of the lookup that raises (User or SportAssociation
	// DoesNotExist); prepared holds the export built from reads only.
	failure  string
	prepared *consumerRestoreExportService
}

// plan_ binds the arguments and performs every read of the task before any
// effect: the user and association lookups and, for an export, the complete
// archive (model files, attachments, manifest, ZIP) in a temporary
// directory, with the progress events it would publish recorded for Run.
// Inputs outside the reproduced subset are handed off: arguments other than
// lowercase canonical UUID strings, archived password hashes the pbkdf2
// hashers do not decide, export documents other rows reference, stored values
// outside the serialized subset, archives needing ZIP64 or over 1 GiB, and
// any read error.
func (s *ConsumerExportAssociation) plan_(ctx context.Context, request *celeryworker.Request) error {
	values, err := consumerPrintBind([]string{"sport_association_id", "user_id", "include_files"}, 2, request)
	if err != nil {
		return err
	}
	association, ok1 := values["sport_association_id"].(string)
	user, ok2 := values["user_id"].(string)
	if !ok1 || !ok2 || !consumerRestoreExportUUID.MatchString(user) {
		return celeryworker.ErrHandoff
	}
	call := &consumerRestoreExportCall{association: association, user: user, associationValid: consumerRestoreExportUUID.MatchString(association)}
	if !call.associationValid {
		return celeryworker.ErrHandoff
	}
	if call.userRow, err = s.store.ExportUser(ctx, user); err != nil {
		return celeryworker.ErrHandoff
	}
	if !call.userRow.Found {
		call.failure = "User matching query does not exist."
		request.State = call
		return nil
	}
	if call.associationRow, err = s.store.ExportAssociation(ctx, association); err != nil {
		return celeryworker.ErrHandoff
	}
	if !call.associationRow.Found {
		call.failure = "SportAssociation matching query does not exist."
		request.State = call
		return nil
	}
	if call.credentials, call.passwords, err = s.credentialPasswords(ctx, association, call.associationRow.Owner); err != nil {
		return celeryworker.ErrHandoff
	}
	if err = s.checkCleanup(ctx, association); err != nil {
		return celeryworker.ErrHandoff
	}
	prepared := &consumerRestoreExportService{s: s, call: call, association: call.associationRow}
	call.prepared = prepared
	request.State = call
	if err = prepared.prepare(ctx); err != nil {
		return celeryworker.ErrHandoff
	}
	return nil
}

// checkCleanup refuses an association whose export documents other rows
// reference (Document.delete() would cascade to them).
func (s *ConsumerExportAssociation) checkCleanup(ctx context.Context, association string) error {
	conn, done, err := s.database(ctx)
	if err != nil {
		return err
	}
	exporter := &associationexport.Exporter{Plan: s.plan, Association: association}
	all, err := exporter.CleanupCandidates(ctx, conn, false)
	done()
	if err != nil {
		return err
	}
	for _, export := range all {
		references, err := s.store.DocumentReferences(ctx, export.Document)
		if err != nil {
			return err
		}
		if references > 0 {
			return errors.New("consumer export: export document referenced by other rows")
		}
		if export.Filepath != "" {
			if _, err := documentkey.S3Key(export.Filepath, s.objects.Location()); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *ConsumerExportAssociation) credentialPasswords(ctx context.Context, association, owner string) (map[string]bool, map[string]bool, error) {
	conn, done, err := s.database(ctx)
	if err != nil {
		return nil, nil, err
	}
	defer done()
	exporter := &associationexport.Exporter{Plan: s.plan, Association: association, Owner: owner}
	passwords, err := exporter.CredentialPasswords(ctx, conn)
	if err != nil {
		return nil, nil, err
	}
	credentials := map[string]bool{}
	restorable := map[string]bool{}
	for id, password := range passwords {
		credentials[id] = true
		ok, supported := restorablePasswordHash(password)
		if !supported {
			return nil, nil, celeryworker.ErrHandoff
		}
		restorable[id] = ok
	}
	return credentials, restorable, nil
}

// restorablePasswordHash is is_restorable_password_hash for unusable or
// empty values and the pbkdf2_sha256/pbkdf2_sha1 hashers; supported is false
// for any other encoding (the configured PASSWORD_HASHERS decide those).
func restorablePasswordHash(encoded string) (ok, supported bool) {
	if encoded == "" || strings.HasPrefix(encoded, "!") {
		return false, true
	}
	algorithm, rest, found := strings.Cut(encoded, "$")
	size := 0
	switch algorithm {
	case "pbkdf2_sha256":
		size = 32
	case "pbkdf2_sha1":
		size = 20
	default:
		return false, false
	}
	if !found {
		return false, true
	}
	parts := strings.SplitN(rest, "$", 3)
	if len(parts) != 3 {
		return false, true
	}
	iterationsText, salt, digest := parts[0], parts[1], parts[2]
	if iterationsText == "" || strings.Trim(iterationsText, "0123456789") != "" {
		return false, false
	}
	iterations, err := strconv.ParseInt(iterationsText, 10, 64)
	if err != nil {
		return false, false
	}
	if salt == "" || digest == "" {
		return false, true
	}
	// Django 5.2's PBKDF2 hashers: iterations = 1_000_000.
	if iterations <= 0 || iterations > 1_000_000 {
		return false, true
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(digest)
	if err != nil {
		return false, false
	}
	return len(decoded) == size, true
}

// consumerRestoreExportPublisher is ExportProgressPublisher.
type consumerRestoreExportPublisher struct {
	s                         *ConsumerExportAssociation
	request                   *celeryworker.Request
	taskID, association, user string
	lastPercent               int
	lastPhase                 string
	lastPublished             time.Time
	mu                        sync.Mutex
}

type consumerRestoreExportProgress struct {
	percent          int
	phase, label     string
	completed, total any
	extra            associationexport.Dict
}

func (p *consumerRestoreExportPublisher) snapshot(status string, progress consumerRestoreExportProgress, extra associationexport.Dict) associationexport.Dict {
	percent := max(0, min(100, progress.percent))
	normalized := associationexport.Dict{{Key: "percent", Value: percent}, {Key: "phase", Value: progress.phase},
		{Key: "label", Value: progress.label}, {Key: "completed", Value: progress.completed}, {Key: "total", Value: progress.total}}
	normalized = append(normalized, progress.extra...)
	snapshot := associationexport.Dict{{Key: "task_id", Value: p.taskID}, {Key: "sport_association_id", Value: p.association},
		{Key: "user_id", Value: p.user}, {Key: "status", Value: status}, {Key: "ready", Value: status == "SUCCESS" || status == "FAILURE"},
		{Key: "estimate", Value: strconv.Itoa(percent) + "%"}, {Key: "progress", Value: normalized},
		{Key: "updated_at", Value: associationexport.PythonISOFormat(p.s.now())}}
	return append(snapshot, extra...)
}

// publish is ExportProgressPublisher.publish: monotonic percent, a 1 s
// throttle within a phase, the PROGRESS state (update_state) and active
// cache entry, or the terminal last-export entry, then the WebSocket event.
func (p *consumerRestoreExportPublisher) publish(ctx context.Context, progress consumerRestoreExportProgress, event, status string, force bool,
	extra associationexport.Dict) {
	p.mu.Lock()
	defer p.mu.Unlock()
	percent := max(0, min(100, progress.percent))
	now := time.Now()
	changed := progress.phase != p.lastPhase
	if percent < p.lastPercent {
		return
	}
	if !force {
		if percent == p.lastPercent && !changed {
			return
		}
		if !changed && now.Sub(p.lastPublished) < consumerRestoreExportThrottle {
			return
		}
	}
	snapshot := p.snapshot(status, progress, extra)
	if status == "PROGRESS" {
		if encoded, err := celery.EncodeJSON(toCeleryValue(snapshot)); err == nil {
			_ = p.s.results.StoreResult(ctx, p.taskID, "PROGRESS", string(encoded), `{"children": []}`)
		}
		_ = p.s.cache.Set(ctx, "association-export-active:"+p.association, snapshot, consumerRestoreExportActiveTimeout)
	} else {
		_ = p.s.cache.Set(ctx, "association-export-last:"+p.association, snapshot, consumerRestoreExportLastTimeout)
	}
	if p.s.groups != nil {
		_ = p.s.groups.GroupSend(ctx, "notifications_user_"+p.user, channels.Dict{{Key: "type", Value: event},
			{Key: "payload", Value: toChannelsValue(snapshot)}})
	}
	p.lastPercent, p.lastPhase, p.lastPublished = percent, progress.phase, now
}

func toCeleryValue(value any) any {
	switch v := value.(type) {
	case associationexport.Dict:
		out := make(celery.Dict, len(v))
		for i, item := range v {
			out[i] = celery.Item{Key: item.Key, Value: toCeleryValue(item.Value)}
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = toCeleryValue(item)
		}
		return out
	case int:
		return int64(v)
	case associationexport.Int:
		return celery.Number(v)
	}
	return value
}

func toChannelsValue(value any) any {
	switch v := value.(type) {
	case associationexport.Dict:
		out := make(channels.Dict, len(v))
		for i, item := range v {
			out[i] = channels.Item{Key: item.Key, Value: toChannelsValue(item.Value)}
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = toChannelsValue(item)
		}
		return out
	case associationexport.Int:
		n, _ := strconv.ParseInt(string(v), 10, 64)
		return n
	}
	return value
}

// consumerRestoreExportFailure is a Python exception the body raises (str(e) and
// the class for the logs).
type consumerRestoreExportFailure struct{ message string }

func (f *consumerRestoreExportFailure) Error() string { return f.message }

func (s *ConsumerExportAssociation) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, _ := request.State.(*consumerRestoreExportCall)
	if call == nil {
		return nil, errors.New("consumer export: no plan")
	}
	publisher := &consumerRestoreExportPublisher{s: s, request: request, taskID: request.ID, association: call.association, user: call.user,
		lastPercent: -1}
	publisher.publish(ctx, consumerRestoreExportProgress{percent: 1, phase: "preparing", label: "Preparazione export"}, "export_progress",
		"PROGRESS", true, nil)
	result, err := s.export(ctx, request, call, publisher)
	if err != nil {
		var failure *consumerRestoreExportFailure
		if !errors.As(err, &failure) {
			s.clearActive(ctx, call.association, request.ID)
			return nil, err
		}
		publisher.publish(ctx, consumerRestoreExportProgress{percent: max(publisher.lastPercent, 0), phase: "failed", label: "Export non riuscito"},
			"export_failed", "FAILURE", true, associationexport.Dict{{Key: "error", Value: "Si è verificato un errore durante l’export dei dati."}})
		if call.userRow.Found {
			s.sendMail(ctx, request, "Errore export dati",
				"Si è verificato un errore durante l'export dei dati. Riprova più tardi o contatta il supporto.", call.userRow.Email, call.association)
		}
		s.clearActive(ctx, call.association, request.ID)
		return celery.Dict{{Key: "success", Value: false}, {Key: "error", Value: failure.message}}, nil
	}
	s.clearActive(ctx, call.association, request.ID)
	return result, nil
}

// clearActive is clear_active_export(association, task_id).
func (s *ConsumerExportAssociation) clearActive(ctx context.Context, association, taskID string) {
	key := "association-export-active:" + association
	current, present, isDict, err := s.cache.TaskOf(ctx, key)
	if err != nil {
		return
	}
	if present && isDict {
		if text, ok := current.(string); !ok || text != taskID {
			return
		}
	}
	_ = s.cache.Delete(ctx, key)
}

func (s *ConsumerExportAssociation) sendMail(ctx context.Context, request *celeryworker.Request, subject, message, recipient, association string) {
	root := request.RootID
	if root == "" {
		root = request.ID
	}
	id, err := s.producer.Publish(ctx, celery.Task{Name: "core.tasks.send_mail_async", Kwargs: celery.Dict{
		{Key: "subject", Value: subject}, {Key: "message", Value: message}, {Key: "from_email", Value: s.settings.DefaultFromEmail},
		{Key: "recipient_list", Value: []any{recipient}}, {Key: "sport_association_id", Value: association}},
		RootID: root, ParentID: request.ID})
	if err == nil {
		request.AddChild(id)
	}
}

type consumerRestoreExportDocument struct {
	ID, Filename, Filepath string
	FilepathSet            bool
	Created                time.Time
	Medical, Invoice, File bool
}

func (s *ConsumerExportAssociation) export(ctx context.Context, request *celeryworker.Request, call *consumerRestoreExportCall,
	publisher *consumerRestoreExportPublisher) (any, error) {
	if call.failure != "" {
		return nil, &consumerRestoreExportFailure{call.failure}
	}
	user, association, service := call.userRow, call.associationRow, call.prepared
	if service == nil {
		return nil, errors.New("consumer export: archive not prepared")
	}
	if err := s.cleanup(ctx, call.association); err != nil {
		return nil, err
	}
	publisher.publish(ctx, consumerRestoreExportProgress{percent: 5, phase: "preparing", label: "Preparazione dati completata"}, "export_progress",
		"PROGRESS", true, nil)
	service.publisher = publisher
	for _, event := range service.events {
		publisher.publish(ctx, event, "export_progress", "PROGRESS", false, nil)
	}
	document, err := service.saveToStorage(ctx, service.zipPath, service.zipName)
	if err != nil {
		return nil, err
	}
	firstName := user.FirstName
	if firstName == "" {
		firstName = "Utente"
	}
	var message strings.Builder
	message.WriteString("\nCiao " + firstName + ",\n\nL'export dei dati della tua associazione \"" + association.Denomination +
		"\" è stato completato con successo.\n\nPuoi scaricare il file dalla sezione \"Archivio Documenti\" del tuo pannello di controllo.\n\nNome file: " +
		document.filename + "\n\nStatistiche export:\n")
	for _, stat := range service.stats {
		message.WriteString("  - " + stat.Name + ": " + strconv.FormatInt(stat.Count, 10) + "\n")
	}
	if len(service.errors) > 0 {
		message.WriteString("\nAvvisi:\n")
		for _, line := range service.errors {
			message.WriteString("  - " + line + "\n")
		}
	}
	message.WriteString("\nGrazie,\nIl team " + s.settings.WhitelabelName + "\n")
	s.sendMail(ctx, request, "Export completato - "+association.Denomination, message.String(), user.Email, call.association)
	publisher.publish(ctx, consumerRestoreExportProgress{percent: 100, phase: "completed", label: "Export completato", completed: 1, total: 1},
		"export_completed", "SUCCESS", true, associationexport.Dict{{Key: "document", Value: associationexport.Dict{
			{Key: "document_id", Value: document.id}, {Key: "filename", Value: document.filename}}}})
	stats := make(celery.Dict, 0, len(service.stats))
	for _, stat := range service.stats {
		stats = append(stats, celery.Item{Key: stat.Name, Value: stat.Count})
	}
	return celery.Dict{{Key: "success", Value: true}, {Key: "document_id", Value: document.id}, {Key: "filename", Value: document.filename},
		{Key: "stats", Value: stats}}, nil
}

// cleanup is cleanup_old_exports(association, max_count=2): exports older
// than 30 days, then every export after the two newest by date.
func (s *ConsumerExportAssociation) cleanup(ctx context.Context, association string) error {
	conn, done, err := s.database(ctx)
	if err != nil {
		return err
	}
	exporter := &associationexport.Exporter{Plan: s.plan, Association: association}
	all, err := exporter.CleanupCandidates(ctx, conn, false)
	done()
	if err != nil {
		return err
	}
	cutoff := s.now().Add(-consumerRestoreExportMaxAgeDays * 24 * time.Hour)
	for _, export := range all {
		if export.Created.Before(cutoff) {
			if err = s.deleteExport(ctx, export); err != nil {
				return err
			}
		}
	}
	conn, done, err = s.database(ctx)
	if err != nil {
		return err
	}
	excess, err := exporter.CleanupCandidates(ctx, conn, true)
	done()
	if err != nil {
		return err
	}
	for _, export := range excess {
		if err = s.deleteExport(ctx, export); err != nil {
			return err
		}
	}
	return nil
}

func (s *ConsumerExportAssociation) deleteExport(ctx context.Context, export associationexport.CleanupExport) error {
	if export.Filepath != "" {
		key, err := documentkey.S3Key(export.Filepath, s.objects.Location())
		if err != nil {
			return err
		}
		if err = s.objects.Delete(ctx, key); err != nil {
			return nil // logged by Django, the loop continues with the next export
		}
	}
	// export.delete() then document.delete(); Django logs a failure of either
	// and continues with the next export.
	if err := s.store.DeleteArchive(ctx, export.Archive, export.Association, s.now()); err != nil {
		return nil
	}
	_ = s.store.DeleteDocument(ctx, export.Document, s.now())
	return nil
}

// consumerRestoreExportService is AssociationExportService.export().
type consumerRestoreExportService struct {
	s           *ConsumerExportAssociation
	publisher   *consumerRestoreExportPublisher
	call        *consumerRestoreExportCall
	association repository.ExportAssociation
	stats       []associationexport.Stat
	errors      []string
	lastPercent int
	// events are the progress reports of the prepared phases, replayed by Run.
	events           []consumerRestoreExportProgress
	dir              string
	zipPath, zipName string
}

func (e *consumerRestoreExportService) remove() {
	if e.dir != "" {
		_ = os.RemoveAll(e.dir)
	}
	if e.zipPath != "" {
		_ = os.Remove(e.zipPath)
	}
}

type consumerRestoreExportDocumentResult struct{ id, filename string }

// report is _report_progress: monotonic, capped at 99, through the
// publisher.
func (e *consumerRestoreExportService) report(ctx context.Context, percent int, phase, label string, completed, total any, extra associationexport.Dict) {
	percent = max(e.lastPercent, min(99, max(0, percent)))
	e.lastPercent = percent
	event := consumerRestoreExportProgress{percent: percent, phase: phase, label: label, completed: completed, total: total, extra: extra}
	if e.publisher == nil {
		e.events = append(e.events, event)
		return
	}
	e.publisher.publish(ctx, event, "export_progress", "PROGRESS", false, nil)
}

// prepare is export() up to create_zip, from reads only.
func (e *consumerRestoreExportService) prepare(ctx context.Context) error {
	s := e.s
	e.lastPercent = -1
	e.report(ctx, 5, "preparing", "Preparazione dati", nil, nil, nil)
	dir, err := os.MkdirTemp(s.settings.TempDir, "tmp")
	if err != nil {
		return err
	}
	e.dir = dir
	for _, sub := range []string{"data", "system", "files"} {
		if err = os.MkdirAll(filepath.Join(dir, sub), 0o777); err != nil {
			return err
		}
	}
	conn, done, err := s.database(ctx)
	if err != nil {
		return err
	}
	exporter := &associationexport.Exporter{Plan: s.plan, Association: e.call.association, Owner: e.association.Owner,
		Credentials: e.call.credentials, PasswordFor: func(id string) bool { return e.call.passwords[id] },
		Progress: func(index, total int, name string) {
			e.report(ctx, 5+50*index/max(total, 1), "model_serialization", "Esportazione dati: "+name, index, total,
				associationexport.Dict{{Key: "model", Value: name}})
		}}
	serialized, err := exporter.Run(ctx, conn, dir)
	if err != nil {
		done()
		return err
	}
	e.stats = serialized.Stats
	e.errors = serialized.Errors
	documents, err := exporter.Files(ctx, conn, serialized.Documents)
	if err != nil {
		done()
		return err
	}
	signatures, err := exporter.Signatures(ctx, conn)
	done()
	if err != nil {
		return err
	}
	if err = e.exportFiles(ctx, dir, documents, signatures, len(serialized.Documents)); err != nil {
		return err
	}
	if err = e.manifest(dir); err != nil {
		return err
	}
	e.report(ctx, 87, "zip_creation", "Preparazione archivio ZIP", nil, nil, nil)
	zipName := "export_" + e.call.association + "_" + s.now().UTC().Format("20060102_150405") + ".zip"
	e.zipName = zipName
	e.zipPath = filepath.Join(s.settings.TempDir, zipName)
	if err = e.createZip(ctx, dir, e.zipPath); err != nil {
		return err
	}
	info, err := os.Stat(e.zipPath)
	if err != nil {
		return err
	}
	if info.Size() > consumerRestoreExportArchiveMaxSize {
		return errors.New("consumer export: archive over the reproduced size")
	}
	return nil
}

func (e *consumerRestoreExportService) exportFiles(ctx context.Context, dir string, documents []consumerRestoreExportFile,
	signatures []consumerRestoreExportSignature, documentTotal int) error {
	s := e.s
	filesDir := filepath.Join(dir, "files")
	var count, failed, skipped int64
	completed := 0
	for _, doc := range documents {
		e.copyDocument(ctx, filesDir, doc, &count, &failed, &skipped)
		completed++
		e.report(ctx, 55+25*completed/max(documentTotal, 1), "file_retrieval", "Recupero allegati", completed, documentTotal, nil)
	}
	var sigCount, sigFailed int64
	for _, signature := range signatures {
		filename := signature.Key[strings.LastIndex(signature.Key, "/")+1:]
		if filename == "" {
			filename = "signature.png"
		}
		target := filepath.Join(filesDir, "subscription_signatures", signature.Subscription)
		err := os.MkdirAll(target, 0o777)
		if err == nil {
			err = s.copyObject(ctx, signature.Key, target+"/"+filename)
		}
		switch {
		case err == nil:
			sigCount++
		case s.missing(err):
			sigFailed++
		default:
			e.errors = append(e.errors, "Failed to export signature for subscription "+signature.Subscription)
			sigFailed++
		}
		e.report(ctx, 82, "file_retrieval", "Recupero firme iscrizioni", sigCount+sigFailed, nil, nil)
	}
	e.stats = append(e.stats, associationexport.Stat{Name: "signature_files_exported", Count: sigCount},
		associationexport.Stat{Name: "signature_files_failed", Count: sigFailed})
	count += sigCount
	failed += sigFailed
	e.stats = append(e.stats, associationexport.Stat{Name: "files_exported", Count: count},
		associationexport.Stat{Name: "files_failed", Count: failed}, associationexport.Stat{Name: "files_skipped", Count: skipped})
	e.report(ctx, 85, "file_retrieval", "Recupero allegati completato", count+failed+skipped, nil, nil)
	return nil
}

func (e *consumerRestoreExportService) copyDocument(ctx context.Context, filesDir string, doc consumerRestoreExportFile, count, failed, skipped *int64) {
	s := e.s
	path := doc.Filepath
	if !doc.FilepathSet || path == "" {
		if doc.Filename == "" {
			*skipped++
			return
		}
		// str(creation_date.timestamp()) / document_id / filename (STORAGE_DIR is empty).
		path = associationexport.PythonFloatRepr(float64(doc.Created.UnixMicro())/1e6) + "/" + doc.ID + "/" + doc.Filename
	}
	category := "general_documents"
	switch {
	case doc.Medical:
		category = "medical_certificates"
	case doc.Invoice:
		category = "invoices"
	case doc.File:
		category = "subscription_documents"
	}
	target := filepath.Join(filesDir, category, doc.ID)
	err := os.MkdirAll(target, 0o777)
	name := doc.Filename
	if name == "" {
		name = "file"
	}
	if err == nil {
		err = s.copyObject(ctx, path, target+"/"+name)
	}
	switch {
	case err == nil:
		*count++
	case s.missing(err):
		if doc.FilepathSet && doc.Filepath != "" {
			*failed++
		} else {
			*skipped++
		}
	default:
		e.errors = append(e.errors, "Failed to export document "+doc.ID)
		*failed++
	}
}

func (s *ConsumerExportAssociation) missing(err error) bool {
	return errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ENOENT) || s.objects.Missing(err)
}

// copyObject is `with default_storage.open(key) as src, open(dest, 'wb')`:
// the object is read first, then the local file is created.
func (s *ConsumerExportAssociation) copyObject(ctx context.Context, name, dest string) error {
	if strings.ContainsRune(dest, 0) {
		return errors.New("embedded null byte")
	}
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return err
	}
	body, err := s.objects.Read(ctx, key)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o666)
	if err != nil {
		return err
	}
	if _, err = file.Write(body); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}

func (e *consumerRestoreExportService) manifest(dir string) error {
	s := e.s
	statistics := associationexport.Dict{}
	counts := map[string]int64{}
	for _, stat := range e.stats {
		statistics = append(statistics, associationexport.Item{Key: stat.Name, Value: stat.Count})
		counts[stat.Name] = stat.Count
	}
	models := make([]any, 0, len(s.plan.Models))
	for _, model := range s.plan.Models {
		models = append(models, associationexport.Dict{{Key: "name", Value: model.Name}, {Key: "count", Value: counts[model.Name]},
			{Key: "file", Value: model.File}})
	}
	var errorsValue any
	if len(e.errors) > 0 {
		list := make([]any, len(e.errors))
		for i, line := range e.errors {
			list[i] = line
		}
		errorsValue = list
	}
	var taxCode any
	if e.association.TaxCode != nil {
		taxCode = *e.association.TaxCode
	}
	manifest := associationexport.Dict{{Key: "version", Value: "1.0.0"}, {Key: "export_format", Value: "bakney_sport_export_v1"},
		{Key: "export_date", Value: associationexport.PythonISOFormat(s.now())}, {Key: "source_environment", Value: "production"},
		{Key: "association", Value: associationexport.Dict{{Key: "sport_association_id", Value: e.call.association},
			{Key: "denomination", Value: e.association.Denomination}, {Key: "tax_code", Value: taxCode}}},
		{Key: "statistics", Value: statistics}, {Key: "models_exported", Value: models},
		{Key: "includes_deleted_records", Value: true}, {Key: "errors", Value: errorsValue}}
	encoded, err := associationexport.AppendJSONIndent(nil, manifest, 2)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "manifest.json"), encoded, 0o666)
}

// createZip is create_zip: os.walk order (the directory's own listing order,
// files before descending), ZipFile.write of every file.
func (e *consumerRestoreExportService) createZip(ctx context.Context, dir, path string) error {
	var entries []string
	if err := pythonWalk(dir, &entries); err != nil {
		return err
	}
	out, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0o666)
	if err != nil {
		return err
	}
	defer out.Close()
	writer := pyzip.NewFileWriter(out)
	for index, entry := range entries {
		info, err := os.Stat(entry)
		if err != nil {
			return err
		}
		arcname, err := filepath.Rel(dir, entry)
		if err != nil {
			return err
		}
		if err = writer.WriteFile(filepath.ToSlash(arcname), entry, info.Mode(), info.ModTime().UTC()); err != nil {
			return err
		}
		e.report(ctx, 87+8*(index+1)/max(len(entries), 1), "zip_creation", "Creazione archivio ZIP", index+1, len(entries), nil)
	}
	if err = writer.Close(); err != nil {
		return err
	}
	return out.Close()
}

// pythonWalk is os.walk(top) (topdown, scandir order) flattened to files.
func pythonWalk(top string, out *[]string) error {
	dir, err := os.Open(top)
	if err != nil {
		return err
	}
	names, err := dir.Readdirnames(-1)
	dir.Close()
	if err != nil {
		return err
	}
	var subdirs []string
	for _, name := range names {
		path := top + "/" + name
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		if info.IsDir() {
			subdirs = append(subdirs, path)
		} else {
			*out = append(*out, path)
		}
	}
	for _, sub := range subdirs {
		if err := pythonWalk(sub, out); err != nil {
			return err
		}
	}
	return nil
}

func (e *consumerRestoreExportService) saveToStorage(ctx context.Context, zipPath, filename string) (consumerRestoreExportDocumentResult, error) {
	s := e.s
	id := s.ids()
	document := domain.NewDocument{ID: id, Token: s.ids(), Filename: filename, Created: s.now().UTC().Truncate(time.Microsecond)}
	if err := s.store.CreateDocument(ctx, document, s.now()); err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	storagePath := "exports/" + s.now().UTC().Format("20060102_150405") + "/" + id + "/" + filename
	info, err := os.Stat(zipPath)
	if err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	e.report(ctx, 96, "storage_upload", "Caricamento archivio", 0, 1, nil)
	name, err := documentkey.SaveName(storagePath)
	if err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	if !s.overwrite {
		exists, err := s.objects.Exists(ctx, key)
		if err != nil {
			return consumerRestoreExportDocumentResult{}, err
		}
		if exists {
			return consumerRestoreExportDocumentResult{}, errors.New("consumer export: storage name taken")
		}
	}
	file, err := os.Open(zipPath)
	if err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	body, err := io.ReadAll(file)
	file.Close()
	if err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	if err = s.objects.Put(ctx, key, body, "application/zip", ""); err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	if err = s.store.SetDocumentFile(ctx, id, name, info.Size(), s.now()); err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	created := s.now()
	if err = s.store.CreateArchive(ctx, domain.ArchiveRecord{ID: s.ids(), Association: e.call.association, Document: id}, created, s.now()); err != nil {
		return consumerRestoreExportDocumentResult{}, err
	}
	e.report(ctx, 99, "storage_upload", "Archivio caricato", 1, 1, nil)
	return consumerRestoreExportDocumentResult{id: id, filename: filename}, nil
}

type consumerRestoreExportFile = associationexport.FileDocument
type consumerRestoreExportSignature = associationexport.SignatureFile
