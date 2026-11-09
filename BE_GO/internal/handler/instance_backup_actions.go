package handler

import (
	"net/http"
	"net/url"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// InstanceBackupActionsHandler serves the owner's backup actions:
// BackupItemView (PATCH, DELETE), BackupScheduleView (PUT),
// BackupRestoreView (POST), BackupUploadView (POST, the body streamed to the
// runner) and the remote copy views. Plain APIViews with IsInstanceOwner
// before method dispatch.
type InstanceBackupActionsHandler struct {
	*InstanceAdminHandler
	actions *service.InstanceBackupActions
}

func NewInstanceBackupActionsHandler(admin *InstanceAdminHandler, actions *service.InstanceBackupActions) *InstanceBackupActionsHandler {
	return &InstanceBackupActionsHandler{InstanceAdminHandler: admin, actions: actions}
}

const backupUploadDoc = "Add an archive from the owner's computer, streamed to the runner as it arrives.\n\n" +
	"The body is the raw .tar.gz (not a form): core.asgi hands it over unread,\n" +
	"so a large file never sits in memory or in a temporary file of the API."

// backupActionsBegin is initial() with IsInstanceOwner; it returns the actor.
func (h *InstanceBackupActionsHandler) backupActionsBegin(c echo.Context, allow, operation string) (*domain.Account, error) {
	c.Response().Header().Set("Allow", allow)
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return nil, err
	}
	allowed, err := h.admin.Allowed(c.Request().Context(), *identity.Actor)
	if err != nil {
		return nil, h.fail(c, err)
	}
	if !allowed {
		return nil, c.JSON(403, map[string]string{"detail": instanceAdminDenied})
	}
	return identity.Actor, nil
}

// backupActionsWrite admits a write (operation tracking, audit context);
// with parse it also reads request.data (form bodies flagged).
func (h *InstanceBackupActionsHandler) backupActionsWrite(c echo.Context, actor *domain.Account, parse bool,
	run func(service.BackupActionsRequest, any, bool) (*service.BackupActionsReply, error)) error {
	request := c.Request()
	if parse {
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		c.SetRequest(request.WithContext(write.Context))
		reply, err := run(service.BackupActionsRequest{Actor: actor.ID, Audit: write.Audit}, write.Body, write.Form)
		return h.backupActionsReply(c, reply, err)
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	auditContext, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	c.SetRequest(request.WithContext(ctx))
	reply, err := run(service.BackupActionsRequest{Actor: actor.ID, Audit: auditContext}, nil, false)
	return h.backupActionsReply(c, reply, err)
}

func (h *InstanceBackupActionsHandler) backupActionsReply(c echo.Context, reply *service.BackupActionsReply, err error) error {
	if err != nil {
		return h.fail(c, err)
	}
	if reply.NoStore {
		c.Response().Header().Set("Cache-Control", "no-store")
	}
	if reply.Invalid != nil {
		return c.Blob(400, "application/json", dto.PythonJSONDumpsCompact(backupActionsMessages(c.Request(), reply.Invalid)))
	}
	if reply.Body == nil {
		// DRF renders None as no content and drops the Content-Type.
		c.Response().WriteHeader(reply.Status)
		return nil
	}
	return c.Blob(reply.Status, "application/json", dto.PythonJSONDumpsCompact(reply.Body))
}

// backupActionsName is the <str:name> path value, percent-decoded.
func backupActionsName(c echo.Context) (string, bool) {
	name := c.Param("name")
	if strings.Contains(name, "%") {
		decoded, err := url.PathUnescape(name)
		if err != nil {
			return "", false
		}
		name = decoded
	}
	return name, name != "" && !strings.Contains(name, "/")
}

// Item is BackupItemView on admin/backups/<str:name>.
func (h *InstanceBackupActionsHandler) Item(c echo.Context) error {
	name, ok := backupActionsName(c)
	if !ok {
		return echo.ErrNotFound
	}
	actor, err := h.backupActionsBegin(c, "PATCH, DELETE, OPTIONS", "instance_backup_item")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Backup Item", "Keep (exclude from rotation) or delete one archive.")
	case http.MethodPatch, http.MethodDelete:
		patch := c.Request().Method == http.MethodPatch
		return h.backupActionsWrite(c, actor, true, func(request service.BackupActionsRequest, body any, form bool) (*service.BackupActionsReply, error) {
			if form && strings.HasPrefix(c.Request().Header.Get("Content-Type"), "multipart/") {
				return nil, service.ErrInstanceBackupsNotPorted
			}
			if patch {
				return h.actions.Keep(c.Request().Context(), request, name, body)
			}
			return h.actions.Delete(c.Request().Context(), request, name, body)
		})
	}
	return h.notAllowed(c)
}

// serializerWrite runs a JSON-bodied serializer view; form and multipart
// bodies are refused.
func (h *InstanceBackupActionsHandler) serializerWrite(c echo.Context, actor *domain.Account,
	run func(service.BackupActionsRequest, any) (*service.BackupActionsReply, error)) error {
	return h.backupActionsWrite(c, actor, true, func(request service.BackupActionsRequest, body any, form bool) (*service.BackupActionsReply, error) {
		if form {
			return nil, service.ErrInstanceBackupsNotPorted
		}
		return run(request, body)
	})
}

// Schedule is BackupScheduleView (PUT).
func (h *InstanceBackupActionsHandler) Schedule(c echo.Context) error {
	actor, err := h.backupActionsBegin(c, "PUT, OPTIONS", "instance_backup_schedule")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Backup Schedule", "")
	case http.MethodPut:
		return h.serializerWrite(c, actor, func(request service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.Schedule(c.Request().Context(), request, body)
		})
	}
	return h.notAllowed(c)
}

// Restore is BackupRestoreView (POST) on admin/backups/<str:name>/restore.
func (h *InstanceBackupActionsHandler) Restore(c echo.Context) error {
	name, ok := backupActionsName(c)
	if !ok {
		return echo.ErrNotFound
	}
	actor, err := h.backupActionsBegin(c, "POST, OPTIONS", "instance_backup_restore")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Backup Restore", "Restore one server archive; the runner backs up the current data first.")
	case http.MethodPost:
		return h.serializerWrite(c, actor, func(request service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.Restore(c.Request().Context(), request, name, body)
		})
	}
	return h.notAllowed(c)
}

// Upload is BackupUploadView (POST). As core.asgi's stream_uploads wrapper,
// identity sees an empty body and the view reads the real one as it arrives.
func (h *InstanceBackupActionsHandler) Upload(c echo.Context) error {
	original := c.Request()
	streamed := original.Method == http.MethodPost
	if streamed {
		empty := original.Clone(original.Context())
		empty.Body, empty.ContentLength = http.NoBody, 0
		c.SetRequest(empty)
	}
	actor, err := h.backupActionsBegin(c, "POST, OPTIONS", "instance_backup_upload")
	if streamed {
		c.SetRequest(original.WithContext(c.Request().Context()))
	}
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Backup Upload", backupUploadDoc)
	case http.MethodPost:
		return h.backupActionsWrite(c, actor, false, func(request service.BackupActionsRequest, _ any, _ bool) (*service.BackupActionsReply, error) {
			length := c.Request().ContentLength
			if c.Request().Header.Get("Content-Length") == "" {
				length = 0
			}
			return h.actions.Upload(c.Request().Context(), request, length, c.Request().Body)
		})
	}
	return h.notAllowed(c)
}

// RemoteSettings is RemoteBackupSettingsView (GET, PUT; HEAD is the GET).
func (h *InstanceBackupActionsHandler) RemoteSettings(c echo.Context) error {
	actor, err := h.backupActionsBegin(c, "GET, PUT, HEAD, OPTIONS", "instance_backup_remote")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Remote Backup Settings", "")
	case http.MethodGet, http.MethodHead:
		reply, err := h.actions.RemoteSettings(c.Request().Context())
		return h.backupActionsReply(c, reply, err)
	case http.MethodPut:
		return h.serializerWrite(c, actor, func(request service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.SaveRemoteSettings(c.Request().Context(), request, body)
		})
	}
	return h.notAllowed(c)
}

// RemoteTest is RemoteBackupTestView (POST).
func (h *InstanceBackupActionsHandler) RemoteTest(c echo.Context) error {
	actor, err := h.backupActionsBegin(c, "POST, OPTIONS", "instance_backup_remote_test")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Remote Backup Test", "")
	case http.MethodPost:
		return h.serializerWrite(c, actor, func(_ service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.TestRemote(c.Request().Context(), body)
		})
	}
	return h.notAllowed(c)
}

// RemoteKey is RemoteBackupKeyView (POST); the request body is not parsed.
func (h *InstanceBackupActionsHandler) RemoteKey(c echo.Context) error {
	actor, err := h.backupActionsBegin(c, "POST, OPTIONS", "instance_backup_remote_key")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Remote Backup Key", "Generate a key pair: the recovery key is in this response only.")
	case http.MethodPost:
		return h.backupActionsWrite(c, actor, false, func(request service.BackupActionsRequest, _ any, _ bool) (*service.BackupActionsReply, error) {
			return h.actions.GenerateRemoteKey(c.Request().Context(), request)
		})
	}
	return h.notAllowed(c)
}

// RemoteKeyConfirm is RemoteBackupKeyConfirmView (POST).
func (h *InstanceBackupActionsHandler) RemoteKeyConfirm(c echo.Context) error {
	actor, err := h.backupActionsBegin(c, "POST, OPTIONS", "instance_backup_remote_key_confirm")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Remote Backup Key Confirm", "")
	case http.MethodPost:
		return h.serializerWrite(c, actor, func(request service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.ConfirmRemoteKey(c.Request().Context(), request, body)
		})
	}
	return h.notAllowed(c)
}

// RemoteFetch is RemoteBackupFetchView (POST).
func (h *InstanceBackupActionsHandler) RemoteFetch(c echo.Context) error {
	actor, err := h.backupActionsBegin(c, "POST, OPTIONS", "instance_backup_remote_fetch")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Remote Backup Fetch", "Bring a remote-only copy back to the server; the key is held in memory only.")
	case http.MethodPost:
		return h.serializerWrite(c, actor, func(request service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.FetchRemote(c.Request().Context(), request, body)
		})
	}
	return h.notAllowed(c)
}

// backupActionsMessages renders serializer error codes in the request's
// language (DRF's Italian catalog; some messages have no translation).
func backupActionsMessages(request *http.Request, errs domain.RequestObject) domain.RequestObject {
	english := restLanguage(request) == "en"
	out := make(domain.RequestObject, 0, len(errs))
	for _, field := range errs {
		codes, _ := field.Value.([]any)
		messages := make([]any, 0, len(codes))
		for _, code := range codes {
			text, _ := code.(string)
			messages = append(messages, backupActionsMessage(text, english))
		}
		out = append(out, domain.RequestField{Name: field.Name, Value: messages})
	}
	return out
}

func backupActionsMessage(code string, english bool) string {
	pick := func(en, it string) string {
		if english {
			return en
		}
		return it
	}
	argument := func(prefix string) (string, bool) {
		if strings.HasPrefix(code, prefix) {
			return strings.TrimPrefix(code, prefix), true
		}
		return "", false
	}
	switch code {
	case dto.BackupActionsRequired:
		return pick("This field is required.", "Campo obbligatorio.")
	case dto.BackupActionsNull:
		return pick("This field may not be null.", "Il campo non può essere nullo.")
	case dto.BackupActionsBlank:
		return pick("This field may not be blank.", "Questo campo non può essere omesso.")
	case dto.BackupActionsString:
		return "Not a valid string."
	case dto.BackupActionsBoolean:
		return "Must be a valid boolean."
	case dto.BackupActionsInteger:
		return pick("A valid integer is required.", "È richiesto un numero intero valido.")
	case dto.BackupActionsStringTooLarge:
		return pick("String value too large.", "Stringa troppo lunga.")
	case dto.BackupActionsPattern:
		return pick("This value does not match the required pattern.", "Questo valore non corrisponde alla sequenza richiesta.")
	case dto.BackupActionsNullCharacter:
		return "Null characters are not allowed."
	case dto.BackupActionsNoData:
		return paymentCategoryValidationMessage("no-data", english)
	}
	if value, ok := argument(dto.BackupActionsMaxLength); ok {
		return pick("Ensure this field has no more than "+value+" characters.", "Assicurati che questo campo non abbia più di "+value+" caratteri.")
	}
	if value, ok := argument(dto.BackupActionsMinLength); ok {
		return pick("Ensure this field has at least "+value+" characters.", "Assicurati che questo campo abbia almeno "+value+" caratteri.")
	}
	if value, ok := argument(dto.BackupActionsMaxValue); ok {
		return pick("Ensure this value is less than or equal to "+value+".", "Assicurati che il valore sia minore o uguale a "+value+".")
	}
	if value, ok := argument(dto.BackupActionsMinValue); ok {
		return pick("Ensure this value is greater than or equal to "+value+".", "Assicurati che il valore sia maggiore o uguale a "+value+".")
	}
	if value, ok := argument(dto.BackupActionsChoice); ok {
		return pick(`"`+value+`" is not a valid choice.`, `"`+value+`" non è una scelta valida.`)
	}
	if value, ok := argument(dto.BackupActionsDictionary); ok {
		return paymentCategoryValidationMessage("dictionary:"+value, english)
	}
	return code
}
