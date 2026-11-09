package handler

import (
	"context"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// InstanceSetupHandler serves the first-run and branding APIViews of
// BE/instance: setup-token/validate, configure and logo
// (SetupTokenOrAuthenticated), reconfigure (IsAuthenticated) and admin/logo
// (OwnerLogoView, IsInstanceOwner). DRF runs authentication and identity,
// then the permission (before OPTIONS and method dispatch), then the view;
// request.data is parsed only when the view reads it.
type InstanceSetupHandler struct {
	setup      *service.InstanceSetup
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInstanceSetupHandler(setup *service.InstanceSetup, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InstanceSetupHandler {
	return &InstanceSetupHandler{setup: setup, actors: actors, identities: identities, audit: audit, operations: operations}
}

const instanceSetupDenied = "Setup token missing or invalid."

var (
	instanceJSONParsers    = []string{"application/json"}
	instanceUploadParsers  = []string{"multipart/form-data", "application/x-www-form-urlencoded"}
	instanceDefaultParsers = []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}
)

func instanceSetupOptions(c echo.Context, name, description string, parses []string) error {
	return c.JSON(200, map[string]any{"name": name, "description": description, "renders": []string{"application/json"}, "parses": parses})
}

// identity runs authentication and identity resolution; anonymous=true for
// the SetupTokenOrAuthenticated views, whose permission decides.
func (h *InstanceSetupHandler) identity(c echo.Context, allow, operation string, anonymous bool) (*domain.ResolvedIdentity, error) {
	c.Response().Header().Set("Allow", allow)
	return messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodPost, operation, anonymous)
}

// setupToken is SetupTokenOrAuthenticated.has_permission; false when the
// denial was written (401 without an authenticated user, else 403).
func (h *InstanceSetupHandler) setupToken(c echo.Context, identity *domain.ResolvedIdentity) (bool, error) {
	request := c.Request()
	allowed, err := h.setup.SetupAllowed(request.Context(), identity.ScopeUser, request.Header.Get("X-Setup-Token"))
	if err != nil {
		return false, restError(c, err)
	}
	if allowed {
		return true, nil
	}
	if identity.ScopeUser == nil {
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		return false, restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
	}
	return false, c.JSON(403, map[string]string{"detail": instanceSetupDenied})
}

func (h *InstanceSetupHandler) notAllowed(c echo.Context) error {
	return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
}

// write admits a write through the data-restore lifecycle and runs it.
func (h *InstanceSetupHandler) write(c echo.Context, run func(context.Context) error) error {
	tracked, err := h.operations.Track(c.Request().Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	return run(ctx)
}

func (h *InstanceSetupHandler) reply(c echo.Context, reply *service.InstanceSetupReply, err error) error {
	var invalid *dto.InstanceSetupInvalid
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, map[string]any{"success": false, "errors": instanceAdminMessages(c.Request(), invalid.Errors)})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(reply.Status, reply.Body)
}

// jsonBody is request.data with parser_classes = [JSONParser]: an empty
// body is {}, another media type is 415.
func jsonBody(c echo.Context) (any, error) {
	request := c.Request()
	if request.Body != nil && request.ContentLength != 0 {
		media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
		if media != "application/json" {
			return nil, &restInputError{Status: 415, Detail: restText(request, "media")}
		}
	}
	return readMessageMutationsBody(c)
}

// Validate is InstanceSetupTokenValidateView (POST).
func (h *InstanceSetupHandler) Validate(c echo.Context) error {
	identity, err := h.identity(c, "POST, OPTIONS", "instance_setup_token_validate", true)
	if identity == nil {
		return err
	}
	if ok, err := h.setupToken(c, identity); !ok {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return instanceSetupOptions(c, "Instance Setup Token Validate", "Validate the first-run setup token without changing instance state.", instanceDefaultParsers)
	case http.MethodPost:
		return h.write(c, func(context.Context) error { return c.JSON(200, map[string]bool{"valid": true}) })
	}
	return h.notAllowed(c)
}

// Configure is InstanceSetupView (POST).
func (h *InstanceSetupHandler) Configure(c echo.Context) error {
	identity, err := h.identity(c, "POST, OPTIONS", "instance_configure", true)
	if identity == nil {
		return err
	}
	if ok, err := h.setupToken(c, identity); !ok {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return instanceSetupOptions(c, "Instance Setup", "POST /instance/configure\nInitial instance setup (only works once).\n"+
			"Protected by setup token during first-run setup wizard.", instanceJSONParsers)
	case http.MethodPost:
	default:
		return h.notAllowed(c)
	}
	return h.write(c, func(ctx context.Context) error {
		configured, err := h.setup.Configured(ctx)
		if err != nil {
			return restError(c, err)
		}
		if configured {
			return c.JSON(400, map[string]any{"success": false, "error": "Instance already configured. Use /instance/reconfigure to update."})
		}
		body, err := jsonBody(c)
		if err != nil {
			return restError(c, err)
		}
		audit, err := h.audit(c.Request())
		if err != nil {
			return restError(c, err)
		}
		// The auditlog actor is the authenticated account, if any.
		audit.ActorID, audit.ActorEmail = nil, nil
		if actor := identity.Actor; actor != nil {
			audit.ActorID, audit.ActorEmail = &actor.ID, &actor.Email
		}
		reply, err := h.setup.Configure(ctx, body, audit)
		return h.reply(c, reply, err)
	})
}

// Reconfigure is InstanceReconfigureView (PUT).
func (h *InstanceSetupHandler) Reconfigure(c echo.Context) error {
	identity, err := h.identity(c, "PUT, OPTIONS", "instance_reconfigure", false)
	if identity == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return instanceSetupOptions(c, "Instance Reconfigure", "PUT /instance/reconfigure\nUpdate instance configuration.\n"+
			"Requires authentication and admin privileges.", instanceJSONParsers)
	case http.MethodPut:
	default:
		return h.notAllowed(c)
	}
	return h.write(c, func(ctx context.Context) error {
		reply, err := h.setup.Reconfigure(ctx, identity.ScopeUser, func() (any, error) { return jsonBody(c) })
		return h.reply(c, reply, err)
	})
}

// Logo is InstanceLogoUploadView (POST).
func (h *InstanceSetupHandler) Logo(c echo.Context) error {
	identity, err := h.identity(c, "POST, OPTIONS", "instance_logo_upload", true)
	if identity == nil {
		return err
	}
	if ok, err := h.setupToken(c, identity); !ok {
		return err
	}
	return h.upload(c, "Instance Logo Upload", "POST /instance/logo\nUpload instance logo.")
}

// OwnerLogo is OwnerLogoView: the upload view behind IsInstanceOwner (the
// authenticated actor). The subclass has no docstring of its own.
func (h *InstanceSetupHandler) OwnerLogo(c echo.Context) error {
	identity, err := h.identity(c, "POST, OPTIONS", "instance_owner_logo", false)
	if identity == nil {
		return err
	}
	allowed, err := h.setup.InstanceOwner(c.Request().Context(), *identity.Actor)
	if err != nil {
		return restError(c, err)
	}
	if !allowed {
		return c.JSON(403, map[string]string{"detail": instanceAdminDenied})
	}
	return h.upload(c, "Owner Logo", "")
}

func (h *InstanceSetupHandler) upload(c echo.Context, name, description string) error {
	switch c.Request().Method {
	case http.MethodOptions:
		return instanceSetupOptions(c, name, description, instanceUploadParsers)
	case http.MethodPost:
	default:
		return h.notAllowed(c)
	}
	return h.write(c, func(ctx context.Context) error {
		file, err := logoUpload(c.Request())
		if err != nil {
			return restError(c, err)
		}
		reply, err := h.setup.UploadLogo(ctx, file)
		return h.reply(c, reply, err)
	})
}

// logoUpload is request.FILES['file'] with parser_classes = [MultiPartParser,
// FormParser]: nil without a file part named 'file' (the last one wins).
// Parts without a usable filename are fields; the content type is the part
// header's lowercased media type.
func logoUpload(request *http.Request) (*service.InstanceSetupUpload, error) {
	if request.Body == nil || request.ContentLength == 0 {
		return nil, nil
	}
	media, params, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	switch media {
	case "application/x-www-form-urlencoded":
		return nil, nil
	case "multipart/form-data":
	default:
		return nil, &restInputError{Status: 415, Detail: restText(request, "media")}
	}
	if params["boundary"] == "" {
		return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid boundary in multipart: None"}
	}
	reader := multipart.NewReader(request.Body, params["boundary"])
	var last *service.InstanceSetupUpload
	for {
		part, err := reader.NextPart()
		if err == io.EOF {
			return last, nil
		}
		if err != nil {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		filename := part.FileName()
		if at := strings.LastIndex(filename, `\`); at >= 0 {
			filename = filename[at+1:]
		}
		if part.FormName() != "file" || filename == "" || filename == "." || filename == ".." {
			_, err = io.Copy(io.Discard, part)
		} else {
			kind := strings.ToLower(strings.TrimSpace(strings.SplitN(part.Header.Get("Content-Type"), ";", 2)[0]))
			upload := &service.InstanceSetupUpload{ContentType: kind}
			limited := io.LimitReader(part, service.InstanceLogoMaximum+1)
			if upload.Content, err = io.ReadAll(limited); err == nil {
				var rest int64
				rest, err = io.Copy(io.Discard, part)
				upload.Size = int64(len(upload.Content)) + rest
			}
			last = upload
		}
		part.Close()
		if err != nil {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
	}
}
