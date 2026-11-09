package handler

import (
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// InstanceBackupsHandler serves BE/instance/backups.py: BackupOverviewView
// (GET) and BackupDownloadTokenView (POST) with IsInstanceOwner, and
// BackupDownloadView (GET, http_method_names get/options, AllowAny without
// authenticators). Plain APIViews: no Cache-Control except on the archive.
type InstanceBackupsHandler struct {
	*InstanceAdminHandler
	backups *service.InstanceBackups
}

func NewInstanceBackupsHandler(admin *InstanceAdminHandler, backups *service.InstanceBackups) *InstanceBackupsHandler {
	return &InstanceBackupsHandler{InstanceAdminHandler: admin, backups: backups}
}

const instanceBackupsDownloadDoc = "Browser navigation cannot send the JWT; a short signed token stands in."

// instanceBackupsBegin is initial() with IsInstanceOwner; it returns the actor.
func (h *InstanceBackupsHandler) instanceBackupsBegin(c echo.Context, allow, operation string) (*domain.Account, error) {
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

func (h *InstanceBackupsHandler) instanceBackupsReply(c echo.Context, reply *service.InstanceBackupsReply, err error) error {
	if err != nil {
		return h.fail(c, err)
	}
	if stream := reply.Stream; stream != nil {
		defer stream.Body.Close()
		header := c.Response().Header()
		header.Set("Content-Type", "application/gzip")
		header.Set("Content-Length", strconv.FormatInt(stream.Length, 10))
		header.Set("Content-Disposition", stream.Disposition)
		header.Set("Cache-Control", "no-store")
		header.Set("Referrer-Policy", "no-referrer")
		c.Response().WriteHeader(reply.Status)
		// Headers are sent: a runner failure now ends the connection early,
		// as Django's StreamingHttpResponse does.
		_, err := io.Copy(c.Response(), stream.Body)
		return err
	}
	if reply.Relayed {
		return c.Blob(reply.Status, "application/json", dto.PythonJSONDumpsCompact(reply.Data))
	}
	return c.Blob(reply.Status, "application/json", dto.PythonJSONDumpsCompact(reply.Body))
}

// Overview is BackupOverviewView (GET, HEAD is the same view; POST starts a
// manual backup and never reads the request body).
func (h *InstanceBackupsHandler) Overview(c echo.Context) error {
	actor, err := h.instanceBackupsBegin(c, "GET, POST, HEAD, OPTIONS", "instance_backups")
	if actor == nil {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return supplierOptions(c, "Backup Overview", "")
	case isRead(request.Method):
		refresh, _ := parseReferenceForm(request.URL.RawQuery).Lookup("refresh")
		reply, err := h.backups.Overview(request.Context(), refresh == "1")
		return h.instanceBackupsReply(c, reply, err)
	case request.Method == http.MethodPost:
		tracked, err := h.operations.Track(request.Context())
		if err != nil {
			return paymentCategoryError(c, err)
		}
		defer tracked.Release()
		audit, err := h.audit(request)
		if err != nil {
			return restError(c, err)
		}
		ctx, err := tracked.Admit()
		if err != nil {
			return paymentCategoryError(c, err)
		}
		reply, err := h.backups.Create(ctx, actor.ID, audit)
		return h.instanceBackupsReply(c, reply, err)
	}
	return h.notAllowed(c)
}

// Token is BackupDownloadTokenView (POST); form and multipart bodies are refused.
func (h *InstanceBackupsHandler) Token(c echo.Context) error {
	actor, err := h.instanceBackupsBegin(c, "POST, OPTIONS", "instance_backup_download_token")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Backup Download Token", "")
	case http.MethodPost:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		if write.Form {
			return h.fail(c, service.ErrInstanceBackupsNotPorted)
		}
		reply, err := h.backups.Token(write.Context, actor.ID, write.Body, write.Audit)
		return h.instanceBackupsReply(c, reply, err)
	}
	return h.notAllowed(c)
}

// Download is BackupDownloadView on <str:name>: no identity, no permission.
func (h *InstanceBackupsHandler) Download(c echo.Context) error {
	name := c.Param("name")
	if strings.Contains(name, "%") {
		decoded, err := url.PathUnescape(name)
		if err != nil {
			return echo.ErrNotFound
		}
		name = decoded
	}
	if name == "" || strings.Contains(name, "/") {
		return echo.ErrNotFound
	}
	request := c.Request()
	header := c.Response().Header()
	header.Set("Allow", "GET, OPTIONS")
	header.Set("Content-Language", restLanguage(request))
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Backup Download", instanceBackupsDownloadDoc)
	case http.MethodGet:
		token, _ := parseReferenceForm(request.URL.RawQuery).Lookup("download_token")
		text, _ := token.(string)
		reply, err := h.backups.Download(request.Context(), name, text)
		return h.instanceBackupsReply(c, reply, err)
	}
	return h.notAllowed(c)
}
