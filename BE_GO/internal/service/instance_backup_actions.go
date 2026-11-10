package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

const (
	backupActionsNewer       = "Il servizio di aggiornamento richiede una versione più recente."
	backupActionsNotFound    = "Backup non trovato."
	backupActionsBadRequest  = "Richiesta non valida."
	backupActionsWord        = "RIPRISTINA"
	backupActionsDefault     = "Aggiornamento non disponibile."
	backupActionsUploadError = "Caricamento non riuscito."
	// remote_backups.SLOW: S3 round trips take longer.
	backupActionsSlow   = 30 * time.Second
	backupActionsNormal = 5 * time.Second
)

// InstanceBackupActionsRunner is updater_client's call_runner with a payload
// and send_runner_stream.
type InstanceBackupActionsRunner interface {
	CallJSON(ctx context.Context, method, path string, payload []byte, timeout time.Duration) (*updaterrunner.Reply, error)
	SendStream(ctx context.Context, path string, body io.Reader, length int64) (*updaterrunner.Reply, error)
}

// InstanceBackupActions is the owner's backup actions of BE/instance/backups.py
// (BackupItemView, BackupScheduleView, BackupRestoreView, BackupUploadView)
// and BE/instance/remote_backups.py.
type InstanceBackupActions struct {
	store     *repository.InstanceBackups
	runner    InstanceBackupActionsRunner
	hashes    InstanceBackupsHasher
	now       func() time.Time
	requestID func() string
	limits    BackupActionsLimits
}

// BackupActionsLimits are the settings the views read: the largest upload
// (ASSOZETA_BACKUP_UPLOAD_MAX_BYTES) and plain http:// remote endpoints
// (ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP).
type BackupActionsLimits struct {
	UploadMaxBytes int64
	AllowHTTP      bool
}

func NewInstanceBackupActions(store *repository.InstanceBackups, runner InstanceBackupActionsRunner, hashes InstanceBackupsHasher,
	now func() time.Time, requestID func() string, limits BackupActionsLimits) *InstanceBackupActions {
	return &InstanceBackupActions{store: store, runner: runner, hashes: hashes, now: now, requestID: requestID, limits: limits}
}

// BackupActionsReply is one DRF Response: Body rendered as JSON (None renders
// no content and no Content-Type), or Invalid serializer error codes (400).
type BackupActionsReply struct {
	Status  int
	Body    any
	Invalid domain.RequestObject
	NoStore bool
}

// BackupActionsRequest is the actor and the audit context of one request.
type BackupActionsRequest struct {
	Actor string
	Audit domain.LoginAuditContext
}

func backupActionsError(status int, message string) *BackupActionsReply {
	return &BackupActionsReply{Status: status, Body: domain.RequestObject{{Name: "error", Value: message}}}
}

// backupActionsOutcome is call_runner's result: the decoded success body, a
// rejection (UpdaterRejected) or the unavailable message (UpdaterUnavailable).
type backupActionsOutcome struct {
	value       any
	status      int
	rejected    bool
	message     any
	unavailable string
}

// decode is json.loads(response.read()) then the status check of
// call_runner/send_runner_stream: a non-object rejection body is the
// AttributeError Django lets escape.
func backupActionsDecode(reply *updaterrunner.Reply, err error, fallback string) (*backupActionsOutcome, error) {
	if message, ok := updaterrunner.IsUnavailable(err); ok {
		return &backupActionsOutcome{unavailable: message}, nil
	}
	if err != nil {
		return nil, err
	}
	value, decoded, err := instanceBackupsJSON(reply.Body)
	if err != nil {
		return nil, err
	}
	if !decoded {
		return &backupActionsOutcome{unavailable: updaterrunner.UnreachableText}, nil
	}
	if reply.Status >= 400 {
		body, ok := value.(domain.RequestObject)
		if !ok {
			return nil, errInstanceBackupsUnhandled // body.get: AttributeError
		}
		message, found := body.Lookup("error")
		if !found {
			message = fallback
		}
		return &backupActionsOutcome{status: reply.Status, rejected: true, message: message}, nil
	}
	return &backupActionsOutcome{value: value, status: reply.Status}, nil
}

func (s *InstanceBackupActions) call(ctx context.Context, method, path string, payload any, timeout time.Duration) (*backupActionsOutcome, error) {
	var encoded []byte
	if payload != nil {
		encoded = dto.PythonJSONDumps(payload)
	}
	reply, err := s.runner.CallJSON(ctx, method, path, encoded, timeout)
	return backupActionsDecode(reply, err, backupActionsDefault)
}

// relay is backups.relay: the runner's answer with the success status, its
// Italian message on refusal (an older runner for /backups and
// /backups/schedule), 503 when unavailable.
func (s *InstanceBackupActions) relay(ctx context.Context, method, path string, payload any, success int) (*BackupActionsReply, error) {
	outcome, err := s.call(ctx, method, path, payload, backupActionsNormal)
	if err != nil {
		return nil, err
	}
	switch {
	case outcome.unavailable != "":
		return backupActionsError(503, outcome.unavailable), nil
	case outcome.rejected && outcome.status == 404 && (path == "/backups" || path == "/backups/schedule"):
		return backupActionsError(409, backupActionsNewer), nil
	case outcome.rejected:
		return backupActionsRejection(outcome), nil
	}
	return &BackupActionsReply{Status: success, Body: outcome.value}, nil
}

// remoteRelay is remote_backups.relay: an older runner only for 'Unknown
// endpoint.', Cache-Control: no-store on success.
func (s *InstanceBackupActions) remoteRelay(ctx context.Context, method, path string, payload any, success int, timeout time.Duration) (*BackupActionsReply, error) {
	outcome, err := s.call(ctx, method, path, payload, timeout)
	if err != nil {
		return nil, err
	}
	switch {
	case outcome.unavailable != "":
		return backupActionsError(503, outcome.unavailable), nil
	case outcome.rejected && outcome.status == 404 && dto.PythonStr(outcome.message) == "Unknown endpoint.":
		return backupActionsError(409, backupActionsNewer), nil
	case outcome.rejected:
		return backupActionsRejection(outcome), nil
	}
	return &BackupActionsReply{Status: success, Body: outcome.value, NoStore: true}, nil
}

// backupActionsRejection keeps a 4xx status, other statuses are 503.
func backupActionsRejection(outcome *backupActionsOutcome) *BackupActionsReply {
	status := 503
	if outcome.status >= 400 && outcome.status < 500 {
		status = outcome.status
	}
	return backupActionsError(status, dto.PythonStr(outcome.message))
}

// audit is backups.audit: the UPDATE entry, then the response is rendered (a
// non-finite runner number fails rendering after the entry is written).
func (s *InstanceBackupActions) audit(ctx context.Context, request BackupActionsRequest, action, value string,
	extra domain.RequestObject) error {
	additional := append(domain.RequestObject{{Name: "actor_id", Value: request.Actor}}, extra...)
	if !dto.AuditFiniteJSON(additional) {
		return errInstanceBackupsUnhandled // PostgreSQL rejects the Infinity/NaN JSON tokens
	}
	target, err := s.store.LogTarget(ctx)
	if err != nil {
		return err
	}
	if target == nil {
		return errInstanceBackupsUnhandled // log_create(None): no content type
	}
	return s.store.AppendBackupAction(ctx, repository.InstanceBackupAction{Target: *target, ActorID: request.Actor,
		Changes:    dto.PythonJSONDumps(domain.RequestObject{{Name: action, Value: []any{"", value}}}),
		Additional: dto.PythonJSONDumps(additional), Audit: request.Audit, At: s.now()})
}

// rendered is the strict JSON renderer: non-finite numbers are a ValueError.
func backupActionsRendered(reply *BackupActionsReply) (*BackupActionsReply, error) {
	if !dto.AuditFiniteJSON(reply.Body) {
		return nil, errInstanceBackupsUnhandled
	}
	return reply, nil
}

func backupActionsInvalid(errs domain.RequestObject) *BackupActionsReply {
	return &BackupActionsReply{Status: 400, Invalid: errs}
}

// Keep is BackupItemView.patch.
func (s *InstanceBackupActions) Keep(ctx context.Context, request BackupActionsRequest, name string, body any) (*BackupActionsReply, error) {
	if !dto.InstanceBackupActionsArchive.MatchString(name) {
		return backupActionsError(404, backupActionsNotFound), nil
	}
	var keep any
	if object, ok := body.(domain.RequestObject); ok {
		keep, _ = object.Lookup("keep")
	}
	flag, ok := keep.(bool)
	if !ok {
		return backupActionsError(400, backupActionsBadRequest), nil
	}
	if !flag {
		// Back into the rotation: the archive may then be deleted automatically.
		if refusal, err := s.passwordConfirmation(ctx, request.Actor, body); refusal != nil || err != nil {
			return refusal, err
		}
	}
	reply, err := s.relay(ctx, "PATCH", "/backups/"+name, domain.RequestObject{{Name: "keep", Value: flag}}, 200)
	if err != nil || reply.Status != 200 {
		return reply, err
	}
	action := "backup_unkeep"
	if flag {
		action = "backup_keep"
	}
	if err := s.audit(ctx, request, action, name, domain.RequestObject{{Name: "backup", Value: name}}); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// Delete is BackupItemView.delete.
func (s *InstanceBackupActions) Delete(ctx context.Context, request BackupActionsRequest, name string, body any) (*BackupActionsReply, error) {
	if !dto.InstanceBackupActionsArchive.MatchString(name) {
		return backupActionsError(404, backupActionsNotFound), nil
	}
	var confirm any = false
	if object, ok := body.(domain.RequestObject); ok {
		if value, found := object.Lookup("confirm"); found {
			confirm = value
		}
	}
	flag, ok := confirm.(bool)
	if !ok {
		return backupActionsError(400, backupActionsBadRequest), nil
	}
	if refusal, err := s.passwordConfirmation(ctx, request.Actor, body); refusal != nil || err != nil {
		return refusal, err
	}
	reply, err := s.relay(ctx, "DELETE", "/backups/"+name, domain.RequestObject{{Name: "confirm", Value: flag}}, 200)
	if err != nil || reply.Status != 200 {
		return reply, err
	}
	if err := s.audit(ctx, request, "backup_delete", name, domain.RequestObject{{Name: "backup", Value: name}}); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// Schedule is BackupScheduleView.put.
func (s *InstanceBackupActions) Schedule(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	value, errs, err := dto.ValidateBackupActions(body, dto.BackupScheduleFields)
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	reply, err := s.relay(ctx, "PUT", "/backups/schedule", value, 200)
	if err != nil || reply.Status != 200 {
		return reply, err
	}
	if err := s.audit(ctx, request, "backup_schedule", string(dto.PythonJSONDumpsSorted(value)), nil); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// passwordRefusal is backups.password_refusal on the shared attempts key.
func (s *InstanceBackupActions) passwordRefusal(ctx context.Context, actor, password string) (*BackupActionsReply, error) {
	status, message, err := instanceBackupsPasswordRefusal(ctx, s.store, s.hashes, actor, password)
	if err != nil || status == 0 {
		return nil, err
	}
	return backupActionsError(status, message), nil
}

// passwordConfirmation is backups.password_confirmation: PasswordSerializer on
// the request data (400 with its field errors), then password_refusal.
func (s *InstanceBackupActions) passwordConfirmation(ctx context.Context, actor string, body any) (*BackupActionsReply, error) {
	value, errs, err := dto.ValidateBackupActions(body, dto.BackupPasswordFields)
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	password, _ := value.Lookup("password")
	return s.passwordRefusal(ctx, actor, password.(string))
}

// backupActionsWithout is the validated data without one field (dict.pop).
func backupActionsWithout(value domain.RequestObject, name string) (domain.RequestObject, any) {
	out := domain.RequestObject{}
	var removed any
	for _, field := range value {
		if field.Name == name {
			removed = field.Value
			continue
		}
		out = append(out, field)
	}
	return out, removed
}

// Restore is BackupRestoreView.post.
func (s *InstanceBackupActions) Restore(ctx context.Context, request BackupActionsRequest, name string, body any) (*BackupActionsReply, error) {
	if !dto.InstanceBackupActionsArchive.MatchString(name) {
		return backupActionsError(404, backupActionsNotFound), nil
	}
	value, errs, err := dto.ValidateBackupActions(body, dto.BackupRestoreFields)
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	confirm, _ := value.Lookup("confirm")
	if confirm != backupActionsWord {
		return backupActionsError(400, "Scrivi RIPRISTINA per confermare."), nil
	}
	password, _ := value.Lookup("password")
	refusal, err := s.passwordRefusal(ctx, request.Actor, password.(string))
	if refusal != nil || err != nil {
		return refusal, err
	}
	reply, err := s.relay(ctx, "POST", "/backups/"+name+"/restore", domain.RequestObject{{Name: "request_id", Value: s.requestID()},
		{Name: "actor_id", Value: request.Actor}, {Name: "confirm", Value: backupActionsWord}}, 202)
	if err != nil || reply.Status != 202 {
		return reply, err
	}
	data, ok := reply.Body.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // response.data['operation']: TypeError
	}
	operationValue, found := data.Lookup("operation")
	if !found {
		return nil, errInstanceBackupsUnhandled // KeyError
	}
	operation, ok := operationValue.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // .get: AttributeError
	}
	id, found := operation.Lookup("id")
	if !found {
		id = ""
	}
	if err := s.audit(ctx, request, "backup_restore", name, domain.RequestObject{{Name: "backup", Value: name},
		{Name: "operation", Value: id}}); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// Upload is BackupUploadView.post: length is the request's Content-Length
// (0 when absent) and body its unread stream.
func (s *InstanceBackupActions) Upload(ctx context.Context, request BackupActionsRequest, length int64, body io.Reader) (*BackupActionsReply, error) {
	if length <= 0 {
		return backupActionsError(411, "Seleziona un file da caricare."), nil
	}
	if limit := s.limits.UploadMaxBytes; length > limit {
		return &BackupActionsReply{Status: 413, Body: domain.RequestObject{
			{Name: "error", Value: "Il file supera la dimensione massima consentita per il caricamento."},
			{Name: "max_bytes", Value: json.Number(strconv.FormatInt(limit, 10))}}}, nil
	}
	reply, err := s.runner.SendStream(ctx, "/backups/upload", body, length)
	if errors.Is(err, updaterrunner.ErrUploadAborted) {
		return backupActionsError(400, "Caricamento interrotto. Riprova."), nil
	}
	outcome, err := backupActionsDecode(reply, err, backupActionsUploadError)
	if err != nil {
		return nil, err
	}
	switch {
	case outcome.unavailable != "":
		return backupActionsError(503, outcome.unavailable), nil
	case outcome.rejected && outcome.status == 404:
		return backupActionsError(409, backupActionsNewer), nil
	case outcome.rejected:
		return backupActionsRejection(outcome), nil
	}
	result, ok := outcome.value.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // result.get: AttributeError
	}
	archiveValue, _ := result.Lookup("archive")
	var name any = ""
	if backupActionsTruthy(archiveValue) {
		archive, ok := archiveValue.(domain.RequestObject)
		if !ok {
			return nil, errInstanceBackupsUnhandled // archive.get: AttributeError
		}
		if value, found := archive.Lookup("name"); found {
			name = value
		}
	}
	if err := s.audit(ctx, request, "backup_upload", dto.PythonStr(name), domain.RequestObject{{Name: "backup", Value: name},
		{Name: "size", Value: json.Number(strconv.FormatInt(length, 10))}}); err != nil {
		return nil, err
	}
	return backupActionsRendered(&BackupActionsReply{Status: 201, Body: result})
}

// backupActionsTruthy is bool() of a JSON value.
func backupActionsTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			number, _ := new(big.Int).SetString(string(v), 10)
			return number == nil || number.Sign() != 0
		}
		number, err := strconv.ParseFloat(string(v), 64)
		return err != nil || number != 0
	case []any:
		return len(v) > 0
	case domain.RequestObject:
		return len(v) > 0
	}
	return true
}

// RemoteSettings is RemoteBackupSettingsView.get.
func (s *InstanceBackupActions) RemoteSettings(ctx context.Context) (*BackupActionsReply, error) {
	reply, err := s.remoteRelay(ctx, "GET", "/backups/remote", nil, 200, backupActionsNormal)
	if err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// remoteSettingsFields is remote_backups.SETTINGS_FIELDS.
var remoteSettingsFields = []string{"endpoint", "region", "bucket", "prefix", "access_key", "path_style", "retention", "enabled"}

// SaveRemoteSettings is RemoteBackupSettingsView.put: the audit records what
// changed, the secret only as 'changed'.
func (s *InstanceBackupActions) SaveRemoteSettings(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	validated, errs, err := dto.ValidateBackupActions(body, dto.BackupRemoteSettingsFields(s.limits.AllowHTTP, true))
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	value, password := backupActionsWithout(validated, "password")
	if refusal, err := s.passwordRefusal(ctx, request.Actor, password.(string)); refusal != nil || err != nil {
		return refusal, err
	}
	reply, err := s.remoteRelay(ctx, "PUT", "/backups/remote", value, 200, backupActionsNormal)
	if err != nil || reply.Status != 200 {
		return reply, err
	}
	change := domain.RequestObject{}
	for _, key := range remoteSettingsFields {
		if item, found := value.Lookup(key); found {
			change = append(change, domain.RequestField{Name: key, Value: item})
		}
	}
	if secret, _ := value.Lookup("secret_key"); secret != nil && secret != "" {
		change = append(change, domain.RequestField{Name: "secret_key", Value: "changed"})
	}
	if err := s.audit(ctx, request, "backup_remote_settings", string(dto.PythonJSONDumpsSorted(change)), nil); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// TestRemote is RemoteBackupTestView.post: request.data or {} with the
// owner's password, relayed without 'enabled' and the password (slow
// timeout); on 200 the audit records what was tried, the secret only as
// 'provided'.
func (s *InstanceBackupActions) TestRemote(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	if !backupActionsTruthy(body) {
		body = domain.RequestObject{}
	}
	validated, errs, err := dto.ValidateBackupActions(body, dto.BackupRemoteSettingsFields(s.limits.AllowHTTP, true))
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	value, password := backupActionsWithout(validated, "password")
	if refusal, err := s.passwordRefusal(ctx, request.Actor, password.(string)); refusal != nil || err != nil {
		return refusal, err
	}
	payload, _ := backupActionsWithout(value, "enabled")
	reply, err := s.remoteRelay(ctx, "POST", "/backups/remote/test", payload, 200, backupActionsSlow)
	if err != nil || reply.Status != 200 {
		return reply, err
	}
	tried := domain.RequestObject{}
	for _, key := range remoteSettingsFields {
		if item, found := payload.Lookup(key); found {
			tried = append(tried, domain.RequestField{Name: key, Value: item})
		}
	}
	if secret, _ := payload.Lookup("secret_key"); secret != nil && secret != "" {
		tried = append(tried, domain.RequestField{Name: "secret_key", Value: "provided"})
	}
	if err := s.audit(ctx, request, "backup_remote_test", string(dto.PythonJSONDumpsSorted(tried)), nil); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// GenerateRemoteKey is RemoteBackupKeyView.post: the owner's password, then
// the runner generates the key.
func (s *InstanceBackupActions) GenerateRemoteKey(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	if refusal, err := s.passwordConfirmation(ctx, request.Actor, body); refusal != nil || err != nil {
		return refusal, err
	}
	reply, err := s.remoteRelay(ctx, "POST", "/backups/remote/key", domain.RequestObject{}, 201, backupActionsNormal)
	if err != nil || reply.Status != 201 {
		return reply, err
	}
	data, ok := reply.Body.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // response.data.get: AttributeError
	}
	var keyID any = ""
	if value, found := data.Lookup("key_id"); found {
		keyID = value
	}
	if err := s.audit(ctx, request, "backup_remote_key_generated", dto.PythonStr(keyID), nil); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// ConfirmRemoteKey is RemoteBackupKeyConfirmView.post.
func (s *InstanceBackupActions) ConfirmRemoteKey(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	validated, errs, err := dto.ValidateBackupActions(body, dto.BackupKeyConfirmFields)
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	value, password := backupActionsWithout(validated, "password")
	if refusal, err := s.passwordRefusal(ctx, request.Actor, password.(string)); refusal != nil || err != nil {
		return refusal, err
	}
	reply, err := s.remoteRelay(ctx, "POST", "/backups/remote/key/confirm", value, 200, backupActionsNormal)
	if err != nil || reply.Status != 200 {
		return reply, err
	}
	keyID, _ := value.Lookup("key_id")
	if err := s.audit(ctx, request, "backup_remote_key_confirmed", keyID.(string), nil); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}

// FetchRemote is RemoteBackupFetchView.post: the recovery key is relayed,
// never stored or audited.
func (s *InstanceBackupActions) FetchRemote(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	value, errs, err := dto.ValidateBackupActions(body, dto.BackupFetchFields)
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if errs != nil {
		return backupActionsInvalid(errs), nil
	}
	name, _ := value.Lookup("name")
	recovery, _ := value.Lookup("recovery_key")
	payload := domain.RequestObject{{Name: "name", Value: name}, {Name: "recovery_key", Value: recovery},
		{Name: "request_id", Value: s.requestID()}, {Name: "actor_id", Value: request.Actor}}
	reply, err := s.remoteRelay(ctx, "POST", "/backups/remote/fetch", payload, 202, backupActionsSlow)
	if err != nil || reply.Status != 202 {
		return reply, err
	}
	if err := s.audit(ctx, request, "backup_remote_fetch", name.(string), domain.RequestObject{{Name: "backup", Value: name}}); err != nil {
		return nil, err
	}
	return backupActionsRendered(reply)
}
