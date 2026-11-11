package handler

import (
	"bytes"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// DataRestoreHandler serves the data-restore administration APIViews of
// BE/instance/restore/views.py: DataRestoreView (GET, POST),
// DataRestoreBackupsView (GET), DataRestoreActionView (GET, POST) with
// IsInstanceOwner, and DataRestoreDownloadView (GET, AllowAny, no
// authentication). Every DRF response carries Cache-Control: no-store; the
// parsers are MultiPartParser and JSONParser.
type DataRestoreHandler struct {
	*InstanceAdminHandler
	restore *service.DataRestore
}

func NewDataRestoreHandler(admin *InstanceAdminHandler, restore *service.DataRestore) *DataRestoreHandler {
	return &DataRestoreHandler{InstanceAdminHandler: admin, restore: restore}
}

// dataRestoreUUID is Django's uuid path converter.
var dataRestoreUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func dataRestoreOptions(c echo.Context, name string) error {
	return c.JSON(200, map[string]any{"name": name, "description": "", "renders": []string{"application/json"},
		"parses": []string{"multipart/form-data", "application/json"}})
}

// begin is initial() with IsInstanceOwner; it returns request.user's id.
func (h *DataRestoreHandler) begin(c echo.Context, allow, operation string) (string, bool, error) {
	c.Response().Header().Set("Allow", allow)
	c.Response().Header().Set("Cache-Control", "no-store")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return "", false, err
	}
	allowed, err := h.admin.Allowed(c.Request().Context(), *identity.Actor)
	if err != nil {
		return "", false, h.restoreFail(c, err)
	}
	if !allowed {
		return "", false, c.JSON(403, map[string]string{"detail": instanceAdminDenied})
	}
	return identity.Actor.ID, true, nil
}

func (h *DataRestoreHandler) restoreFail(c echo.Context, err error) error {
	var input *restInputError
	switch {
	case errors.Is(err, domain.ErrDataRestoreNotFound):
		return c.JSON(404, map[string]string{"detail": domain.ErrDataRestoreNotFound.Error()})
	case errors.As(err, &input):
		return restError(c, err)
	}
	// An unhandled exception: Django's error page has no DRF headers.
	c.Response().Header().Del("Cache-Control")
	return restError(c, err)
}

func (h *DataRestoreHandler) respond(c echo.Context, reply *service.DataRestoreReply, err error) error {
	if err != nil {
		return h.restoreFail(c, err)
	}
	header := c.Response().Header()
	if reply.ReferrerPolicy != "" {
		header.Set("Referrer-Policy", reply.ReferrerPolicy)
	}
	switch {
	case reply.Stream != nil:
		stream := reply.Stream
		defer stream.Object.Body.Close()
		header.Set("Content-Type", stream.ContentType)
		if stream.ContentLength != nil {
			header.Set("Content-Length", strconv.FormatInt(*stream.ContentLength, 10))
		}
		if stream.ContentEncoding != "" {
			header.Set("Content-Encoding", stream.ContentEncoding)
		}
		header.Set("Content-Disposition", stream.Disposition)
		c.Response().WriteHeader(reply.Status)
		// Headers are sent: a provider failure now ends the connection early,
		// as Django's StreamingHttpResponse does.
		_, err = io.Copy(c.Response(), stream.Object.Body)
		return err
	case reply.Raw != nil:
		header.Set("Content-Disposition", reply.Disposition)
		return c.Blob(reply.Status, "application/json", reply.Raw)
	}
	return c.Blob(reply.Status, "application/json", dto.PythonJSONDumpsCompact(reply.Body))
}

// Root is DataRestoreView (GET, POST).
func (h *DataRestoreHandler) Root(c echo.Context) error {
	user, ok, err := h.begin(c, "GET, POST, HEAD, OPTIONS", "instance_data_restore")
	if !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return dataRestoreOptions(c, "Data Restore")
	case isRead(request.Method):
		reply, err := h.restore.Overview(request.Context(), user)
		return h.respond(c, reply, err)
	case request.Method == http.MethodPost:
		return h.write(c, func() error {
			reply, err := h.restore.Upload(c.Request().Context(), user, func(limit int64) ([]byte, error) { return h.uploadedFile(c, limit) })
			return h.respond(c, reply, err)
		})
	}
	return h.notAllowed(c)
}

// Backups is DataRestoreBackupsView (GET).
func (h *DataRestoreHandler) Backups(c echo.Context) error {
	user, ok, err := h.begin(c, "GET, HEAD, OPTIONS", "instance_data_restore_backups")
	if !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return dataRestoreOptions(c, "Data Restore Backups")
	case isRead(request.Method):
		before, _ := parseReferenceForm(request.URL.RawQuery).Lookup("before")
		text, _ := before.(string)
		reply, err := h.restore.Backups(request.Context(), user, text)
		return h.respond(c, reply, err)
	}
	return h.notAllowed(c)
}

// Action is DataRestoreActionView (GET, POST) on <uuid:operation_id>/<str:action>.
func (h *DataRestoreHandler) Action(c echo.Context) error {
	id, action := c.Param("id"), c.Param("action")
	if !dataRestoreUUID.MatchString(id) || action == "" {
		return echo.ErrNotFound
	}
	user, ok, err := h.begin(c, "GET, POST, HEAD, OPTIONS", "instance_data_restore_action")
	if !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return dataRestoreOptions(c, "Data Restore Action")
	case isRead(request.Method):
		reply, err := h.restore.Read(request.Context(), user, id, action)
		return h.respond(c, reply, err)
	case request.Method == http.MethodPost:
		return h.write(c, func() error {
			reply, err := h.restore.Act(c.Request().Context(), user, id, action, func() (any, error) { return h.data(c) })
			return h.respond(c, reply, err)
		})
	}
	return h.notAllowed(c)
}

// Download is DataRestoreDownloadView (GET; AllowAny without authenticators).
func (h *DataRestoreHandler) Download(c echo.Context) error {
	id := c.Param("id")
	if !dataRestoreUUID.MatchString(id) {
		return echo.ErrNotFound
	}
	request := c.Request()
	header := c.Response().Header()
	header.Set("Allow", "GET, HEAD, OPTIONS")
	header.Set("Cache-Control", "no-store")
	header.Set("Content-Language", restLanguage(request))
	switch {
	case request.Method == http.MethodOptions:
		return dataRestoreOptions(c, "Data Restore Download")
	case isRead(request.Method):
		token, present := parseReferenceForm(request.URL.RawQuery).Lookup("download_token")
		text, isText := token.(string)
		reply, err := h.restore.Download(request.Context(), id, text, present && isText)
		return h.respond(c, reply, err)
	}
	return h.notAllowed(c)
}

// write tracks the request for graceful shutdown like the other writes.
func (h *DataRestoreHandler) write(c echo.Context, run func() error) error {
	tracked, err := h.operations.Track(c.Request().Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	c.SetRequest(c.Request().WithContext(ctx))
	return run()
}

const dataRestoreBodyKey = "data_restore_body"

type dataRestoreBody struct {
	raw []byte
	err error
}

// rawBody reads the request body once (DATA_UPLOAD_MAX_MEMORY_SIZE does not
// apply to multipart file parts; Django spools them).
func (h *DataRestoreHandler) rawBody(c echo.Context) ([]byte, error) {
	if cached, ok := c.Get(dataRestoreBodyKey).(dataRestoreBody); ok {
		return cached.raw, cached.err
	}
	var raw []byte
	var err error
	if request := c.Request(); request.Body != nil && request.ContentLength != 0 {
		raw, err = io.ReadAll(request.Body)
		request.Body = io.NopCloser(bytes.NewReader(raw))
	}
	c.Set(dataRestoreBodyKey, dataRestoreBody{raw: raw, err: err})
	return raw, err
}

// data is request.data with [MultiPartParser, JSONParser]; other media types
// are not ported.
func (h *DataRestoreHandler) data(c echo.Context) (any, error) {
	raw, err := h.rawBody(c)
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 {
		return domain.RequestObject{}, nil
	}
	media, params, _ := mime.ParseMediaType(c.Request().Header.Get("Content-Type"))
	switch media {
	case "application/json":
		return readMessageMutationsBody(c)
	case "multipart/form-data":
		return parseDjangoMultipart(raw, params["boundary"])
	}
	// Neither parser takes the media type: DRF's UnsupportedMediaType.
	return nil, &restInputError{Status: 415, Detail: restText(c.Request(), "media")}
}

// uploadedFile is request.FILES.get('file') with LimitedRestoreUploadHandler
// first: file parts count towards one received total, and the part that
// passes the limit and everything after it are dropped (StopUpload).
func (h *DataRestoreHandler) uploadedFile(c echo.Context, limit int64) ([]byte, error) {
	raw, err := h.rawBody(c)
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 {
		return nil, nil
	}
	media, params, _ := mime.ParseMediaType(c.Request().Header.Get("Content-Type"))
	switch media {
	case "application/json":
		_, err := readMessageMutationsBody(c)
		return nil, err
	case "multipart/form-data":
	default:
		return nil, &restInputError{Status: 415, Detail: restText(c.Request(), "media")}
	}
	boundary := params["boundary"]
	if boundary == "" {
		return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid boundary in multipart: None"}
	}
	reader := multipart.NewReader(bytes.NewReader(raw), boundary)
	var received int64
	var found []byte
	for {
		part, err := reader.NextRawPart()
		if err == io.EOF {
			return found, nil
		}
		if err != nil {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		content, err := io.ReadAll(part)
		if err != nil {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		if part.Header.Get("Content-Transfer-Encoding") != "" {
			return nil, domain.ErrDataRestoreNotPorted
		}
		disposition := part.Header.Get("Content-Disposition")
		if disposition == "" {
			continue
		}
		parameters, ok := djangoHeaderParameters(disposition)
		if !ok {
			return nil, domain.ErrDataRestoreNotPorted
		}
		name, named := parameters["name"]
		filename := parameters["filename"]
		if !named || filename == "" {
			continue
		}
		sanitized, ok := djangoUploadName(filename)
		if !ok {
			return nil, domain.ErrDataRestoreNotPorted
		}
		if sanitized == "" {
			continue
		}
		received += int64(len(content))
		if received > limit {
			return found, nil
		}
		if strings.TrimFunc(name, unicode.IsSpace) == "file" {
			found = content
			if found == nil {
				found = []byte{}
			}
		}
	}
}
