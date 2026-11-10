package handler

import (
	"bytes"
	"context"
	"errors"
	"io"
	"math"
	"mime"
	"mime/multipart"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyhtml"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/labstack/echo/v4"
)

type DocumentUploadService interface {
	ParseUUID(raw string) (string, error)
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) ([]any, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, read func() (any, error), audit domain.LoginAuditContext) error
	UploadTarget(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error
	UploadDocument(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any, audit domain.LoginAuditContext) error
	CertificateDocument(ctx context.Context, identity domain.ResolvedIdentity, body any, session string, audit domain.LoginAuditContext) (string, error)
	MedicalTarget(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error
	MedicalUpload(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any, audit domain.LoginAuditContext) (domain.RequestObject, error)
}

type DocumentUploadsHandler struct {
	uploads    DocumentUploadService
	archive    ArchiveWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
	// MedicalCertificateUploadThrottle: the DRF throttle cache, its hourly
	// rate and the clock (nil cache: no throttle).
	certificateThrottle BakneyThrottleStore
	certificateRate     int
	certificateNow      func() time.Time
}

// WithCertificateThrottle sets MedicalCertificateUploadThrottle's cache and
// MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR.
func (h *DocumentUploadsHandler) WithCertificateThrottle(store BakneyThrottleStore, perHour int, now func() time.Time) *DocumentUploadsHandler {
	h.certificateThrottle, h.certificateRate, h.certificateNow = store, perHour, now
	return h
}

// certificateThrottleWindow is the duration of the '<n>/hour' rate.
const certificateThrottleWindow = 3600

// certificateAllow is MedicalCertificateUploadThrottle.allow_request
// (AnonRateThrottle: only requests without an account, keyed by address)
// plus DRF's Throttled response.
func (h *DocumentUploadsHandler) certificateAllow(c echo.Context, identity *domain.ResolvedIdentity) (bool, error) {
	if h.certificateThrottle == nil || identity.ScopeUser != nil {
		return true, nil
	}
	request := c.Request()
	key := "throttle_medical-certificate-upload_" + throttleIdent(request)
	history, err := h.certificateThrottle.History(request.Context(), key)
	if err != nil {
		return false, messageReadsError(c, err)
	}
	now := float64(h.certificateNow().UnixNano()) / 1e9
	for len(history) > 0 && history[len(history)-1] <= now-certificateThrottleWindow {
		history = history[:len(history)-1]
	}
	if len(history) >= h.certificateRate {
		detail := "Request was throttled."
		if restLanguage(request) != "en" {
			detail = "La richiesta è stata limitata (throttled)."
		}
		remaining := float64(certificateThrottleWindow)
		if len(history) > 0 {
			remaining = certificateThrottleWindow - (now - history[len(history)-1])
		}
		if available := h.certificateRate - len(history) + 1; available > 0 {
			wait := int(math.Ceil(remaining / float64(available)))
			unit := "seconds"
			if wait == 1 {
				unit = "second"
			}
			detail += " Expected available in " + strconv.Itoa(wait) + " " + unit + "."
			c.Response().Header().Set("Retry-After", strconv.Itoa(wait))
		}
		return false, c.JSON(http.StatusTooManyRequests, map[string]string{"detail": detail})
	}
	history = append([]float64{now}, history...)
	if err := h.certificateThrottle.Store(request.Context(), key, history, certificateThrottleWindow*time.Second); err != nil {
		return false, messageReadsError(c, err)
	}
	return true, nil
}

func NewDocumentUploadsHandler(uploads DocumentUploadService, archive ArchiveWriteService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *DocumentUploadsHandler {
	return &DocumentUploadsHandler{uploads: uploads, archive: archive, actors: actors, identities: identities, audit: audit, operations: operations}
}

// djangoBadRequestPage is Django's SuspiciousOperation answer (bad_request view).
var djangoBadRequestPage = []byte("\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Bad Request (400)</title>\n</head>\n<body>\n" +
	"  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n")

func (h *DocumentUploadsHandler) failure(c echo.Context, err error) error {
	var suspicious *documentkey.SuspiciousFileError
	var invalid *service.PaymentInfoUUIDError
	var validation *service.DocumentValidationError
	var add *service.ArchiveAddError
	var medical *service.MedicalUploadError
	request := c.Request()
	switch {
	case errors.As(err, &suspicious):
		c.Response().Header().Del("Allow")
		return c.Blob(400, "text/html; charset=utf-8", djangoBadRequestPage)
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.As(err, &validation):
		return c.JSON(400, documentValidationMessages(request, validation.Fields))
	case errors.As(err, &add):
		return c.JSON(400, map[string]string{"exception": add.Message})
	case errors.As(err, &medical):
		return c.JSON(medical.Status, map[string]string{"error": medical.Message})
	case errors.Is(err, service.ErrUploadDocumentMissing):
		return c.JSON(400, map[string]string{"exception": "document key not present"})
	case errors.Is(err, service.ErrArchiveLookup):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrArchiveNotFound):
		return restError(c, &restInputError{Status: 404, Detail: err.Error()})
	case errors.Is(err, domain.ErrSubscriptionMedicalNotFound):
		return c.JSON(404, map[string]string{"error": "Subscription not found"})
	}
	var input *restInputError
	var denied *domain.PermissionDenied
	if errors.As(err, &input) || errors.As(err, &denied) {
		return restError(c, err)
	}
	return messageReadsError(c, err)
}

func documentValidationMessages(request *http.Request, fields map[string]any) any {
	english := restLanguage(request) == "en"
	result := map[string]any{}
	for name, value := range fields {
		codes, _ := value.([]string)
		messages := make([]string, len(codes))
		for index, code := range codes {
			if code == "archive-date-format" {
				messages[index] = "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
				if english {
					messages[index] = "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
				}
				continue
			}
			messages[index] = paymentCategoryValidationMessage(code, english)
		}
		result[name] = messages
	}
	return result
}

// admit tracks the write, reads the body and the audit context.
func (h *DocumentUploadsHandler) admit(c echo.Context, read func() (any, error)) (context.Context, any, domain.LoginAuditContext, func(), error) {
	request := c.Request()
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return nil, nil, domain.LoginAuditContext{}, nil, paymentCategoryError(c, err)
	}
	var body any
	if read != nil {
		if body, err = read(); err != nil {
			tracked.Release()
			return nil, nil, domain.LoginAuditContext{}, nil, err
		}
	}
	audit, err := h.audit(request)
	if err != nil {
		tracked.Release()
		return nil, nil, domain.LoginAuditContext{}, nil, restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		tracked.Release()
		return nil, nil, domain.LoginAuditContext{}, nil, paymentCategoryError(c, err)
	}
	return ctx, body, audit, tracked.Release, nil
}

// jsonOnly is the viewset's JSONParser-only request.data: another media type is 415.
func jsonOnly(c echo.Context) func() (any, error) {
	return func() (any, error) {
		request := c.Request()
		if media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type")); request.ContentLength != 0 && media != "application/json" {
			return nil, &restInputError{Status: 415, Detail: restText(request, "media")}
		}
		return readMessageMutationsBody(c)
	}
}

const documentArchiveOptionsName = "Document Archive"

// Add mirrors DocumentArchiveViewSet.as_view({'post': 'add'}); the viewset's
// delete() makes DELETE allowed and dispatched without a pk.
func (h *DocumentUploadsHandler) Add(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "documents_add")
	if identity == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return archiveMoveOptions(c)
	case http.MethodDelete:
		return messageReadsError(c, errors.New("archive delete without pk"))
	case http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
	}
	ctx, body, audit, release, err := h.admit(c, jsonOnly(c))
	if err != nil {
		var input *restInputError
		if errors.As(err, &input) {
			return restError(c, err)
		}
		return err
	}
	defer release()
	rows, err := h.uploads.Add(ctx, *identity, body, audit)
	if err != nil {
		return h.failure(c, err)
	}
	return c.Blob(http.StatusCreated, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(domain.RequestObject{{Name: "data", Value: rows}}))
}

// Update mirrors DocumentArchiveViewSet.as_view({'patch': 'update'}): get_object,
// then request.data; DELETE runs the viewset's delete() with this pk.
func (h *DocumentUploadsHandler) Update(c echo.Context) error {
	c.Response().Header().Set("Allow", "PATCH, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "documents_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return c.JSON(200, map[string]any{"name": documentArchiveOptionsName, "description": "", "renders": []string{"application/json"},
			"parses": []string{"application/json"}})
	case http.MethodPatch, http.MethodDelete:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	ctx, _, audit, release, err := h.admit(c, nil)
	if err != nil {
		return err
	}
	defer release()
	if request.Method == http.MethodDelete {
		if err = h.archive.Delete(ctx, *identity, c.Param("pk"), audit); err != nil {
			return h.failure(c, err)
		}
		return c.NoContent(http.StatusNoContent)
	}
	return h.failure(c, h.uploads.Update(ctx, *identity, c.Param("pk"), jsonOnly(c), audit))
}

// UploadDocument follows subscription_upload_document: is_valid_uuid, the
// lookup (500 when missing), then request.data.
func (h *DocumentUploadsHandler) UploadDocument(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_upload_document")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Upload Document"); done {
		return err
	}
	request := c.Request()
	id, err := h.uploads.ParseUUID(c.Param("uid"))
	if err != nil {
		return h.failure(c, err)
	}
	group := request.Header.Get("X-Group-Id")
	if err = h.uploads.UploadTarget(request.Context(), *identity, id, group); err != nil {
		return h.failure(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.uploads.UploadDocument(write.Context, *identity, id, group, write.Body, write.Audit); err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"mgs": "file uploaded"})
}

// CertificateDocument follows medical_certificate_document (AllowAny).
func (h *DocumentUploadsHandler) CertificateDocument(c echo.Context) error {
	raw, err := bufferMultipart(c)
	if err != nil {
		return restError(c, err)
	}
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodPost, "medical_certificate_document", true)
	if identity == nil {
		return err
	}
	// check_throttles runs before the method dispatch.
	if allowed, err := h.certificateAllow(c, identity); !allowed {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Medical Certificate Document"); done {
		return err
	}
	ctx, body, audit, release, err := h.admit(c, uploadBody(c, raw))
	if err != nil {
		var input *restInputError
		if errors.As(err, &input) {
			return restError(c, err)
		}
		return err
	}
	defer release()
	certificate, err := h.uploads.CertificateDocument(ctx, *identity, body, c.Request().Header.Get("X-Impersonation-Id"), audit)
	if err != nil {
		return h.failure(c, err)
	}
	return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(domain.RequestObject{{Name: "msg", Value: "success!"},
		{Name: "uid", Value: certificate}, {Name: "expiring_date", Value: nil}}))
}

// MedicalUpload follows subscription_medical_certificate_upload: is_valid_uuid,
// the target lookup (404), then the try block around request.data.
func (h *DocumentUploadsHandler) MedicalUpload(c echo.Context) error {
	raw, err := bufferMultipart(c)
	if err != nil {
		return restError(c, err)
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_medical_certificate_upload")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Medical Certificate Upload"); done {
		return err
	}
	request := c.Request()
	id, err := h.uploads.ParseUUID(c.Param("uid"))
	if err != nil {
		return h.failure(c, err)
	}
	group := request.Header.Get("X-Group-Id")
	if err = h.uploads.MedicalTarget(request.Context(), *identity, id, group); err != nil {
		return h.failure(c, err)
	}
	ctx, body, audit, release, err := h.admit(c, uploadBody(c, raw))
	if err != nil {
		var input *restInputError
		if errors.As(err, &input) {
			return c.JSON(500, map[string]string{"error": input.Detail}) // ParseError inside the try block
		}
		return err
	}
	defer release()
	response, err := h.uploads.MedicalUpload(ctx, *identity, id, group, body, audit)
	if err != nil {
		return h.failure(c, err)
	}
	return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(response))
}

const uploadBodyLimit = 262144000

// bufferMultipart keeps a multipart body in memory so identity resolution
// and the view's own parse read the same bytes; other bodies stay unread.
func bufferMultipart(c echo.Context) ([]byte, error) {
	request := c.Request()
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if media != "multipart/form-data" || request.Body == nil || request.ContentLength == 0 {
		return nil, nil
	}
	raw, err := io.ReadAll(io.LimitReader(request.Body, uploadBodyLimit+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > uploadBodyLimit {
		return nil, domain.ErrDocumentWritesNotPorted
	}
	request.Body = io.NopCloser(bytes.NewReader(raw))
	return raw, nil
}

// uploadBody is request.data for the default parsers, with multipart files
// kept as domain.RequestFile.
func uploadBody(c echo.Context, raw []byte) func() (any, error) {
	return func() (any, error) {
		if raw == nil {
			return readMessageMutationsBody(c)
		}
		_, params, _ := mime.ParseMediaType(c.Request().Header.Get("Content-Type"))
		return parseDjangoMultipart(raw, params["boundary"])
	}
}

// parseDjangoMultipart is MultiPartParser.parse followed by DRF's merge of
// files into data (QueryDict.get returns the last value, files after fields).
func parseDjangoMultipart(raw []byte, boundary string) (domain.RequestObject, error) {
	if boundary == "" {
		return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid boundary in multipart: None"}
	}
	fields, files := domain.RequestObject{}, domain.RequestObject{}
	put := func(object *domain.RequestObject, name string, value any) {
		for i, field := range *object {
			if field.Name == name {
				(*object)[i].Value = value
				return
			}
		}
		*object = append(*object, domain.RequestField{Name: name, Value: value})
	}
	reader := multipart.NewReader(bytes.NewReader(raw), boundary)
	for {
		part, err := reader.NextRawPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		content, err := io.ReadAll(part)
		if err != nil {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		if part.Header.Get("Content-Transfer-Encoding") != "" {
			return nil, domain.ErrDocumentWritesNotPorted
		}
		disposition := part.Header.Get("Content-Disposition")
		if disposition == "" {
			continue
		}
		params, ok := djangoHeaderParameters(disposition)
		if !ok {
			return nil, domain.ErrDocumentWritesNotPorted
		}
		name, named := params["name"]
		if !named {
			continue
		}
		name = strings.TrimFunc(name, unicode.IsSpace)
		if filename := params["filename"]; filename != "" {
			sanitized, ok := djangoUploadName(filename)
			if !ok {
				return nil, domain.ErrDocumentWritesNotPorted
			}
			if sanitized != "" {
				put(&files, name, domain.RequestFile{Name: sanitized, Content: content})
			}
			continue
		}
		put(&fields, name, string(content))
	}
	for _, file := range files {
		put(&fields, file.Name, file.Value)
	}
	return fields, nil
}

// djangoHeaderParameters is django.utils.http.parse_header_parameters for a
// Content-Disposition value: quoted values only unescape \\ and \", and an
// RFC 2231 name* value is percent-decoded (UTF-8 only; others are refused).
func djangoHeaderParameters(line string) (map[string]string, bool) {
	params := map[string]string{}
	s := ";" + line
	first := true
	for strings.HasPrefix(s, ";") {
		s = s[1:]
		end := strings.Index(s, ";")
		for end > 0 && (strings.Count(s[:end], `"`)-strings.Count(s[:end], `\"`))%2 != 0 {
			next := strings.Index(s[end+1:], ";")
			if next < 0 {
				end = -1
				break
			}
			end += 1 + next
		}
		if end < 0 {
			end = len(s)
		}
		part := strings.TrimSpace(s[:end])
		s = s[end:]
		if first {
			first = false
			continue
		}
		i := strings.Index(part, "=")
		if i < 0 {
			continue
		}
		name := strings.ToLower(strings.TrimSpace(part[:i]))
		encoded := false
		if strings.HasSuffix(name, "*") {
			name = name[:len(name)-1]
			encoded = strings.Count(part, "'") == 2
		}
		value := strings.TrimSpace(part[i+1:])
		if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
			value = strings.ReplaceAll(strings.ReplaceAll(value[1:len(value)-1], `\\`, `\`), `\"`, `"`)
		}
		if encoded {
			pieces := strings.Split(value, "'")
			if !strings.EqualFold(pieces[0], "utf-8") && !strings.EqualFold(pieces[0], "utf8") {
				return nil, false
			}
			decoded, err := url.PathUnescape(strings.ReplaceAll(pieces[2], "+", "%2B"))
			if err != nil || !utf8.ValidString(decoded) {
				return nil, false
			}
			value = decoded
		}
		params[name] = value
	}
	return params, true
}

// djangoUploadName is MultiPartParser.sanitize_file_name followed by
// UploadedFile's name handling (basename, 255-character truncation keeping
// the extension). "" means the file is skipped; ok=false refuses names the
// port does not model (invalid UTF-8).
func djangoUploadName(filename string) (string, bool) {
	if !utf8.ValidString(filename) {
		return "", false
	}
	filename = pyhtml.Unescape(filename)
	filename = filename[strings.LastIndex(filename, "/")+1:]
	filename = filename[strings.LastIndex(filename, `\`)+1:]
	filename = strings.Map(func(r rune) rune {
		if unicode.IsPrint(r) {
			return r
		}
		return -1
	}, filename)
	if filename == "" || filename == "." || filename == ".." {
		return "", true
	}
	runes := []rune(filename)
	if len(runes) > 255 {
		root, ext := pythonSplitext(runes)
		if len(ext) > 255 {
			ext = ext[:255]
		}
		if keep := 255 - len(ext); len(root) > keep {
			root = root[:keep]
		}
		filename = string(root) + string(ext)
	}
	return filename, true
}

// pythonSplitext is posixpath.splitext on a name without separators: leading
// dots do not start an extension.
func pythonSplitext(name []rune) ([]rune, []rune) {
	dot := -1
	for i := len(name) - 1; i >= 0; i-- {
		if name[i] == '.' {
			dot = i
			break
		}
	}
	if dot <= 0 {
		return name, nil
	}
	for i := 0; i < dot; i++ {
		if name[i] != '.' {
			return name[:dot], name[dot:]
		}
	}
	return name, nil
}
