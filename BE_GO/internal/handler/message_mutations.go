package handler

import (
	"context"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
	"golang.org/x/text/encoding/charmap"
)

type MessageMutationsService interface {
	Admit(context.Context, domain.ResolvedIdentity) error
	Add(context.Context, domain.ResolvedIdentity, dto.MessageMutationWrite, domain.LoginAuditContext) error
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}
type MessageMutationsHandler struct {
	mutations  MessageMutationsService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewMessageMutationsHandler(mutations MessageMutationsService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *MessageMutationsHandler {
	return &MessageMutationsHandler{mutations: mutations, actors: actors, identities: identities, audit: audit, operations: operations}
}
func (h *MessageMutationsHandler) Add(c echo.Context) error    { return h.mutate(c, false) }
func (h *MessageMutationsHandler) Delete(c echo.Context) error { return h.mutate(c, true) }
func (h *MessageMutationsHandler) mutate(c echo.Context, deleting bool) error {
	method, operation, name := http.MethodPost, "communication_messages_add", "Communication Messages Add"
	if deleting {
		method, operation, name = http.MethodDelete, "communication_messages_delete", "Communication Messages Delete"
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if err = h.mutations.Admit(request.Context(), *identity); err != nil {
		return messageMutationsError(c, err)
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, name)
	}
	if request.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var input dto.MessageMutationWrite
	if !deleting {
		body, err := readMessageMutationsBody(c)
		if err != nil {
			return restError(c, err)
		}
		input, err = dto.ParseMessageMutationWrite(body)
		if err != nil {
			return messageMutationsError(c, err)
		}
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if deleting {
		err = h.mutations.Delete(operationContext, *identity, c.Param("message_id"), audit)
	} else {
		err = h.mutations.Add(operationContext, *identity, input, audit)
	}
	if err != nil {
		return messageMutationsError(c, err)
	}
	msg := "message added."
	if deleting {
		msg = "message deleted."
	}
	return c.JSON(200, map[string]string{"msg": msg})
}
func messageMutationsError(c echo.Context, err error) error {
	if errors.Is(err, domain.ErrMessageMutationInvalid) {
		return c.JSON(400, map[string]string{"msg": "message not valid."})
	}
	if errors.Is(err, domain.ErrMessageMutationNotFound) {
		return c.JSON(404, map[string]string{"msg": "message not found."})
	}
	if errors.Is(err, domain.ErrMessageMutationForbidden) {
		return messageReadsError(c, domain.ErrMessageReadForbidden)
	}
	return messageReadsError(c, err)
}

// Match MessageReads negotiation and scoped admission order while preserving
// Python surrogate code units for MessageInputSerializer validation.
func messageMutationsIdentity(c echo.Context, actors *service.ActorAuthentication, identities *service.IdentityResolver, method, operation string) (*domain.ResolvedIdentity, error) {
	return messageMutationsIdentityFor(c, actors, identities, method, operation, false)
}

// messageMutationsIdentityFor with anonymous=true serves AllowAny views: an
// unauthenticated request resolves to an identity without a scope user.
func messageMutationsIdentityFor(c echo.Context, actors *service.ActorAuthentication, identities *service.IdentityResolver, method, operation string, anonymous bool) (*domain.ResolvedIdentity, error) {
	request := c.Request()
	// Viewsets with several allowed methods set Allow before resolving.
	if c.Response().Header().Get("Allow") == "" {
		c.Response().Header().Set("Allow", method+", OPTIONS")
	}
	c.Response().Header().Set("Content-Language", restLanguage(request))
	query := parseReferenceForm(request.URL.RawQuery)
	if format, present := query.Lookup("format"); present && format != "" && format != "json" {
		return nil, restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	}
	if !acceptsJSON(request.Header.Get("Accept")) {
		return nil, restError(c, &restInputError{Status: 406, Detail: restText(request, "accept")})
	}
	actor, err := actors.Authenticate(request.Context(), request.Header.Get("Authorization"))
	if err != nil {
		return nil, restError(c, err)
	}
	var body any
	var bodyErr error
	if request.Header.Get("X-Impersonation-Id") != "" || request.Header.Get("User-Id") != "" {
		body, bodyErr = readMessageMutationsBody(c)
	}
	identity, err := identities.Resolve(request.Context(), actor, domain.IdentityRequest{
		Method: request.Method, Path: request.URL.Path, SessionID: request.Header.Get("X-Impersonation-Id"),
		TargetID: request.Header.Get("User-Id"), GroupID: request.Header.Get("X-Group-Id"),
		References: &domain.RequestReferences{Operation: operation, Path: routePathReferences(c), Query: query, Body: body, BodyError: bodyErr},
	})
	if err != nil {
		return nil, restError(c, err)
	}
	if identity.ScopeUser == nil && !anonymous {
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		return nil, restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
	}
	return &identity, nil
}

// Keep the slice's transport corrections local and share the authenticated body
// cache so impersonation resolution and the mutation consume identical values.
func readMessageMutationsBody(c echo.Context) (any, error) {
	return readAuthenticatedRESTBodyUsing(c, func(r *http.Request) (any, error) {
		media, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if media == "application/x-www-form-urlencoded" && r.Body != nil && r.ContentLength != 0 {
			const maximum = 262144000
			raw, err := io.ReadAll(io.LimitReader(r.Body, maximum+1))
			if err != nil {
				return nil, err
			}
			if len(raw) > maximum {
				return nil, &restInputError{Status: 413, Detail: "Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE."}
			}
			charset := strings.ToLower(strings.ReplaceAll(params["charset"], "_", "-"))
			switch charset {
			case "", "utf-8", "utf8":
				// QueryDict falls back to Latin-1 for a raw body that cannot
				// decode as UTF-8; percent escapes still use replacement UTF-8.
				if !utf8.Valid(raw) {
					raw, _ = charmap.ISO8859_1.NewDecoder().Bytes(raw)
				}
				return parseImpersonationQuery(string(raw)), nil
			case "latin1", "latin-1", "iso-8859-1", "iso8859-1":
				// QueryDict decodes the raw body and percent escapes with the
				// selected charset, preserving literal non-ASCII text.
				text, _ := charmap.ISO8859_1.NewDecoder().Bytes(raw)
				fields := domain.RequestObject{}
				positions := map[string]int{}
				decode := func(v string) string {
					// Preserve malformed escapes while decoding each valid escape.
					var out strings.Builder
					for i := 0; i < len(v); i++ {
						if v[i] == '%' && i+2 < len(v) {
							if b, err := url.QueryUnescape(v[i : i+3]); err == nil {
								text, _ := charmap.ISO8859_1.NewDecoder().String(b)
								out.WriteString(text)
								i += 2
								continue
							}
						}
						if v[i] == '+' {
							out.WriteByte(' ')
						} else {
							out.WriteByte(v[i])
						}
					}
					return out.String()
				}
				for _, pair := range strings.Split(string(text), "&") {
					if pair == "" {
						continue
					}
					key, value, _ := strings.Cut(pair, "=")
					key, value = decode(key), decode(value)
					if at, ok := positions[key]; ok {
						fields[at].Value = value
					} else {
						positions[key] = len(fields)
						fields = append(fields, domain.RequestField{Name: key, Value: value})
					}
				}
				return fields, nil
			default:
				return nil, domain.ErrInvalidScopedRequest
			}
		}
		var parserErr error
		body, err := readReferenceBodyJSON(r, func(raw []byte) (any, error) {
			value, e := dto.ParseMessageMutationsJSON(raw)
			parserErr = e
			return value, e
		})
		if errors.Is(parserErr, dto.ErrLoginRateJSONValue) {
			return nil, &restInputError{Status: 400, Detail: "JSON parse error - " + parserErr.Error()}
		}
		return body, err
	})
}
