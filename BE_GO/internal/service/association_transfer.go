package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// AssociationTransfer follows AssociationExportViewSet and
// AssociationImportViewSet (BE/application/views/export_views.py) up to the
// points that need storage, Celery dispatch or cached export snapshots; those
// are refused with domain.ErrAssociationTransferNotPorted before any write.
type AssociationTransfer struct {
	store   domain.AssociationTransferStore
	secret  string
	now     func() time.Time
	options AssociationTransferOptions
}

func NewAssociationTransfer(store domain.AssociationTransferStore, secret string, now func() time.Time) *AssociationTransfer {
	return &AssociationTransfer{store: store, secret: secret, now: now}
}

// AssociationTransferReply is a view's own Response(data, status).
type AssociationTransferReply struct {
	Status int
	Body   any
}

func transferError(status int, message string) *AssociationTransferReply {
	return &AssociationTransferReply{Status: status, Body: map[string]string{"error": message}}
}

const (
	exportMaxCount        = 3 // application.tasks.EXPORT_MAX_COUNT
	exportListLimit       = 20
	downloadTokenSalt     = "docmanager.association-export-download"
	downloadTokenPurpose  = "association-export"
	exportViewDenied      = "Solo il proprietario può visualizzare gli export"
	exportDeleteDenied    = "Solo il proprietario può eliminare gli export"
	exportStartDenied     = "Solo il proprietario può esportare i dati"
	exportNoAssociation   = "Associazione non trovata"
	exportNotFound        = "Export non trovato"
	exportTaskRequired    = "task_id richiesto"
	exportActivePrefix    = ":1:association-export-active:"
	exportLastPrefix      = ":1:association-export-last:"
	exportTaskOwnerPrefix = ":1:association-export-task:"
)

// exportOwner is _check_export_permission then request.user.sport_association.
func (s *AssociationTransfer) exportOwner(ctx context.Context, identity domain.ResolvedIdentity, denied string) (string, *AssociationTransferReply, error) {
	user := identity.ScopeUser
	if user == nil || user.Role != domain.RoleAssociation || identity.Collaborator {
		return "", transferError(403, denied), nil
	}
	association, found, err := s.store.OwnedAssociation(ctx, user.ID)
	if err != nil {
		return "", nil, err
	}
	if !found {
		return "", transferError(404, exportNoAssociation), nil
	}
	return association, nil, nil
}

// Active mirrors active_export.
func (s *AssociationTransfer) Active(ctx context.Context, identity domain.ResolvedIdentity) (*AssociationTransferReply, error) {
	association, reply, err := s.exportOwner(ctx, identity, exportViewDenied)
	if reply != nil || err != nil {
		return reply, err
	}
	if s.options.Writes != nil {
		return s.activeExport(ctx, association, identity.ScopeUser.ID)
	}
	cached, err := s.store.CacheKeysExist(ctx, exportActivePrefix+association, exportLastPrefix+association)
	if err != nil {
		return nil, err
	}
	if cached {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	return &AssociationTransferReply{Status: 200, Body: map[string]bool{"active": false}}, nil
}

// Status mirrors export_status; a registered task (its owner cache entry) is not ported.
func (s *AssociationTransfer) Status(ctx context.Context, identity domain.ResolvedIdentity, task string) (*AssociationTransferReply, error) {
	user := identity.ScopeUser
	if user == nil || user.Role != domain.RoleAssociation || identity.Collaborator {
		return transferError(403, exportViewDenied), nil
	}
	if task == "" {
		return transferError(400, exportTaskRequired), nil
	}
	association, reply, err := s.exportOwner(ctx, identity, exportViewDenied)
	if reply != nil || err != nil {
		return reply, err
	}
	_ = association
	registered, err := s.store.CacheKeysExist(ctx, exportTaskOwnerPrefix+task)
	if err != nil {
		return nil, err
	}
	if registered {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	return transferError(404, exportNotFound), nil
}

// List mirrors list_exports with django.core.signing download tokens; audit
// is the request's auditlog context (a size saved on a legacy export).
func (s *AssociationTransfer) List(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (*AssociationTransferReply, error) {
	association, reply, err := s.exportOwner(ctx, identity, exportViewDenied)
	if reply != nil || err != nil {
		return reply, err
	}
	rows, err := s.store.Exports(ctx, association, exportListLimit)
	if err != nil {
		return nil, err
	}
	timestamp := s.now().Unix()
	exports := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		if row.FileSize == nil && row.Filepath != nil && *row.Filepath != "" {
			if s.options.Writes == nil {
				return nil, domain.ErrAssociationTransferNotPorted
			}
			if row.FileSize, err = s.exportSize(ctx, row.DocumentID, *row.Filepath, paymentCategoryAuditContext(identity, audit)); err != nil {
				return nil, err
			}
		}
		var size any
		if row.FileSize != nil {
			size = *row.FileSize
		}
		token, err := s.downloadToken(row.DocumentID, association, timestamp)
		if err != nil {
			return nil, err
		}
		exports = append(exports, map[string]any{"archive_id": row.ArchiveID, "document_id": row.DocumentID, "filename": row.Filename,
			"file_size_bytes": size, "download_token": token, "date": row.Date.Format("2006-01-02"),
			"created_at": pythonISODateTime(row.Created)})
	}
	return &AssociationTransferReply{Status: 200, Body: map[string]any{"exports": exports, "count": len(exports)}}, nil
}

// pythonISODateTime is datetime.isoformat() of an aware UTC datetime.
func pythonISODateTime(value time.Time) string {
	value = value.UTC()
	text := value.Format("2006-01-02T15:04:05")
	if micro := value.Nanosecond() / 1000; micro != 0 {
		text += "." + strconv.Itoa(1000000 + micro)[1:]
	}
	return text + "+00:00"
}

const base62Alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

func base62(value int64) string {
	if value == 0 {
		return "0"
	}
	sign := ""
	if value < 0 {
		sign, value = "-", -value
	}
	var out []byte
	for value > 0 {
		out = append([]byte{base62Alphabet[value%62]}, out...)
		value /= 62
	}
	return sign + string(out)
}

// downloadToken is signing.dumps(payload, salt=DOWNLOAD_TOKEN_SALT): compact
// JSON, unpadded urlsafe base64, a base62 timestamp and the SHA-256
// salted_hmac signature keyed by sha256(salt + "signer" + SECRET_KEY).
func (s *AssociationTransfer) downloadToken(document, association string, timestamp int64) (string, error) {
	if s.secret == "" {
		return "", errors.New("download tokens need SECRET_KEY")
	}
	payload, err := json.Marshal(struct {
		Document    string `json:"document_id"`
		Association string `json:"sport_association_id"`
		Purpose     string `json:"purpose"`
	}{document, association, downloadTokenPurpose})
	if err != nil {
		return "", err
	}
	value := base64.RawURLEncoding.EncodeToString(payload) + ":" + base62(timestamp)
	key := sha256.Sum256([]byte(downloadTokenSalt + "signer" + s.secret))
	mac := hmac.New(sha256.New, key[:])
	mac.Write([]byte(value))
	return value + ":" + base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), nil
}

// Delete mirrors delete_export; audit is the request's auditlog context.
func (s *AssociationTransfer) Delete(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (*AssociationTransferReply, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		// request.data.get on a non-dict body: AttributeError.
		return nil, errors.New("export delete body is not a dictionary")
	}
	document, _ := object.Lookup("document_id")
	user := identity.ScopeUser
	if user == nil || user.Role != domain.RoleAssociation || identity.Collaborator {
		return transferError(403, exportDeleteDenied), nil
	}
	if !dto.PythonTruthy(document) {
		return transferError(400, "document_id richiesto"), nil
	}
	association, reply, err := s.exportOwner(ctx, identity, exportDeleteDenied)
	if reply != nil || err != nil {
		return reply, err
	}
	id, err := dto.AssociationExportDocument(document)
	if err != nil {
		return nil, err
	}
	if s.options.Writes != nil {
		return s.deleteExport(ctx, association, id, paymentCategoryAuditContext(identity, audit))
	}
	found, err := s.store.ExportArchive(ctx, association, id)
	if err != nil {
		return nil, err
	}
	if !found {
		return transferError(404, exportNotFound), nil
	}
	return nil, domain.ErrAssociationTransferNotPorted
}

// Start mirrors start_export.
func (s *AssociationTransfer) Start(ctx context.Context, identity domain.ResolvedIdentity) (*AssociationTransferReply, error) {
	association, reply, err := s.exportOwner(ctx, identity, exportStartDenied)
	if reply != nil || err != nil {
		return reply, err
	}
	count, err := s.store.ExportCount(ctx, association)
	if err != nil {
		return nil, err
	}
	if count >= exportMaxCount {
		return transferError(409, "Elimina un export prima di crearne uno nuovo"), nil
	}
	if s.options.Writes != nil {
		return s.startExport(ctx, association, identity.ScopeUser.ID)
	}
	// cache.add on the active key fails when the key exists.
	running, err := s.store.CacheKeysExist(ctx, exportActivePrefix+association)
	if err != nil {
		return nil, err
	}
	if running {
		return transferError(409, "Un export è già in corso"), nil
	}
	return nil, domain.ErrAssociationTransferNotPorted
}

// ImportAllowed is SetupTokenOrAuthenticated.has_permission. Without an
// InstanceConfiguration the X-Setup-Token header must equal
// INSTANCE_SETUP_TOKEN (an empty setting denies).
func (s *AssociationTransfer) ImportAllowed(ctx context.Context, identity domain.ResolvedIdentity, setupToken string) (bool, error) {
	configured, owner, err := s.store.PrimaryOwner(ctx)
	if err != nil {
		return false, err
	}
	if !configured {
		if s.options.Writes != nil {
			return setupTokenMatches(setupToken, s.options.SetupToken), nil
		}
		if setupToken != "" {
			return false, domain.ErrAssociationTransferNotPorted
		}
		return false, nil
	}
	user := identity.ScopeUser
	if user == nil {
		return false, nil
	}
	if user.Superuser {
		return true, nil
	}
	return owner != nil && *owner == user.ID, nil
}

var loneSurrogate = regexp.MustCompile(`(?i)\\ud[89a-f][0-9a-f]{2}`)

// ImportStatus mirrors import_status: AsyncResult on the django-db result
// backend. Exception states (FAILURE, REVOKED, RETRY), binary content and
// results Celery's JSON decoder would transform are not ported.
func (s *AssociationTransfer) ImportStatus(ctx context.Context, task string) (*AssociationTransferReply, error) {
	if task == "" {
		return transferError(400, exportTaskRequired), nil
	}
	row, err := s.store.TaskResult(ctx, task)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return &AssociationTransferReply{Status: 200, Body: map[string]any{"task_id": task, "status": "PENDING", "ready": false}}, nil
	}
	if row.Encoding != "utf-8" {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	if row.Meta != nil && *row.Meta != "" {
		meta, err := celeryJSON(*row.Meta)
		if err != nil {
			return nil, err
		}
		object, ok := meta.(map[string]any)
		if !ok {
			return nil, domain.ErrAssociationTransferNotPorted
		}
		children, _ := object["children"].([]any)
		_, status := object["status"]
		if status || len(children) > 0 || (object["children"] != nil && children == nil) {
			return nil, domain.ErrAssociationTransferNotPorted
		}
	}
	switch row.Status {
	case "FAILURE", "REVOKED", "RETRY":
		// The backend rebuilds the exception for every exception state; only a
		// ready one (not RETRY) is answered, as str() of it.
		text, err := celeryExceptionText(row.Result)
		if err != nil {
			return nil, err
		}
		body := map[string]any{"task_id": task, "status": row.Status, "ready": row.Status != "RETRY"}
		if row.Status != "RETRY" {
			body["error"] = text
		}
		return &AssociationTransferReply{Status: 200, Body: body}, nil
	}
	body := map[string]any{"task_id": task, "status": row.Status, "ready": row.Status == "SUCCESS"}
	if row.Status == "SUCCESS" {
		var result any
		if row.Result != nil && *row.Result != "" {
			if result, err = celeryJSON(*row.Result); err != nil {
				return nil, err
			}
			body["result"] = json.RawMessage(strings.TrimSpace(*row.Result))
		} else {
			body["result"] = result
		}
	}
	return &AssociationTransferReply{Status: 200, Body: body}, nil
}

// celeryPlainExceptions are the exception classes Celery finds in a module the
// Django process has loaded whose str() is BaseException's: no arguments is
// "", one is str() of it, several are the tuple's repr. KeyError answers the
// repr of a single argument.
var celeryPlainExceptions = map[string]map[string]bool{
	"builtins": {"Exception": true, "ValueError": true, "TypeError": true, "RuntimeError": true, "IndexError": true, "KeyError": true,
		"AssertionError": true, "ZeroDivisionError": true, "NotImplementedError": true, "LookupError": true, "ArithmeticError": true,
		"OverflowError": true, "RecursionError": true, "MemoryError": true, "BufferError": true, "EOFError": true,
		"AttributeError": true, "NameError": true},
	"django.db.utils": {"Error": true, "InterfaceError": true, "DatabaseError": true, "DataError": true, "OperationalError": true,
		"IntegrityError": true, "InternalError": true, "ProgrammingError": true, "NotSupportedError": true},
	"celery.exceptions": {"TaskRevokedError": true},
}

var errCeleryException = errors.New("stored exception cannot be rebuilt")

// celeryExceptionText is str(exception_to_python(result)) for the stored
// result of a row in an exception state: "None" for a missing or falsy result;
// an error (the 500 page) for a result that is not a dictionary or lacks
// exc_type. Without exc_module Celery creates the class itself; with one, only
// the classes of celeryPlainExceptions are ported, with scalar arguments.
func celeryExceptionText(result *string) (string, error) {
	if result == nil || *result == "" {
		return "None", nil
	}
	value, err := celeryJSON(*result)
	if err != nil {
		return "", err
	}
	if !dto.PythonTruthy(value) {
		return "None", nil
	}
	object, ok := value.(map[string]any)
	if !ok {
		if _, list := value.([]any); list {
			return "", domain.ErrAssociationTransferNotPorted // dict() of a list of pairs
		}
		return "", errCeleryException // dict() of a string, number or True raises
	}
	if len(object) == 0 {
		return "None", nil
	}
	rawType, present := object["exc_type"]
	if !present {
		return "", errCeleryException
	}
	kind, ok := rawType.(string)
	if !ok || strings.ContainsRune(kind, 0) {
		return "", domain.ErrAssociationTransferNotPorted
	}
	switch module := object["exc_module"].(type) {
	case nil:
	case string:
		if !celeryPlainExceptions[module][kind] {
			return "", domain.ErrAssociationTransferNotPorted
		}
		if module != "builtins" || kind != "KeyError" {
			kind = ""
		}
	default:
		return "", domain.ErrAssociationTransferNotPorted
	}
	arguments := []any{""}
	if message, present := object["exc_message"]; present {
		if list, isList := message.([]any); isList {
			arguments = list
		} else {
			arguments = []any{message}
		}
	}
	for _, argument := range arguments {
		switch v := argument.(type) {
		case nil, bool, string:
		case json.Number:
			if strings.ContainsAny(string(v), ".eE") {
				return "", domain.ErrAssociationTransferNotPorted
			}
		default:
			return "", domain.ErrAssociationTransferNotPorted
		}
	}
	switch len(arguments) {
	case 0:
		return "", nil
	case 1:
		if kind == "KeyError" && object["exc_module"] != nil {
			return dto.PythonRepr(arguments[0]), nil
		}
		return dto.PythonStr(arguments[0]), nil
	}
	parts := make([]string, len(arguments))
	for i, argument := range arguments {
		parts[i] = dto.PythonRepr(argument)
	}
	return "(" + strings.Join(parts, ", ") + ")", nil
}

// celeryJSON decodes a stored JSON value; invalid JSON is kombu's DecodeError
// (a 500 page). Values the Python side would render differently (typed
// "__type__" objects, lone surrogates, floats beyond float64) are refused.
func celeryJSON(text string) (any, error) {
	if loneSurrogate.MatchString(text) {
		return nil, domain.ErrAssociationTransferNotPorted
	}
	decoder := json.NewDecoder(bytes.NewReader([]byte(text)))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if decoder.More() {
		return nil, errors.New("extra data after the stored JSON value")
	}
	var walk func(any) error
	walk = func(node any) error {
		switch v := node.(type) {
		case map[string]any:
			if _, typed := v["__type__"]; typed {
				return domain.ErrAssociationTransferNotPorted
			}
			for _, child := range v {
				if err := walk(child); err != nil {
					return err
				}
			}
		case []any:
			for _, child := range v {
				if err := walk(child); err != nil {
					return err
				}
			}
		case json.Number:
			if strings.ContainsAny(string(v), ".eE") {
				number, err := strconv.ParseFloat(string(v), 64)
				if err != nil || math.IsInf(number, 0) {
					return domain.ErrAssociationTransferNotPorted
				}
			}
		}
		return nil
	}
	if err := walk(value); err != nil {
		return nil, err
	}
	return value, nil
}
