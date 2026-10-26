package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type MessageReadsService interface {
	Admit(context.Context, domain.ResolvedIdentity) error
	List(context.Context, domain.ResolvedIdentity) ([]dto.MessageRead, error)
	Detail(context.Context, domain.ResolvedIdentity, string) ([]dto.MessageTransactionRead, error)
}

type MessageReadsHandler struct {
	reads      MessageReadsService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewMessageReadsHandler(reads MessageReadsService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *MessageReadsHandler {
	return &MessageReadsHandler{reads: reads, actors: actors, identities: identities}
}

func (h *MessageReadsHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "communication_messages_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if err = h.reads.Admit(request.Context(), *identity); err != nil {
		return messageReadsError(c, err)
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Communication Messages List")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	result, err := h.reads.List(request.Context(), *identity)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, result)
}

func (h *MessageReadsHandler) Detail(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "communication_messages_detail")
	if identity == nil {
		return err
	}
	request := c.Request()
	if err = h.reads.Admit(request.Context(), *identity); err != nil {
		return messageReadsError(c, err)
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Communication Messages Detail")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	result, err := h.reads.Detail(request.Context(), *identity, c.Param("message_id"))
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, result)
}

func messageReadsOptions(c echo.Context, name string) error {
	return c.JSON(200, map[string]any{
		"name": name, "description": "",
		"renders": []string{"application/json"},
		"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"},
	})
}

// DRF negotiates the query-parameter format before authentication and Accept.
// Keep that ordering local because the shared authenticated helper was built
// for views whose probe order differs.
func messageReadsIdentity(c echo.Context, actors *service.ActorAuthentication, identities *service.IdentityResolver, method, operation string) (*domain.ResolvedIdentity, error) {
	request := c.Request()
	c.Response().Header().Set("Allow", method+", OPTIONS")
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
		body, bodyErr = readAuthenticatedRESTBody(c)
	}
	identity, err := identities.Resolve(request.Context(), actor, domain.IdentityRequest{
		Method: request.Method, Path: request.URL.Path, SessionID: request.Header.Get("X-Impersonation-Id"),
		TargetID: request.Header.Get("User-Id"), GroupID: request.Header.Get("X-Group-Id"),
		References: &domain.RequestReferences{Operation: operation, Query: query, Body: body, BodyError: bodyErr},
	})
	if err != nil {
		return nil, restError(c, err)
	}
	if identity.ScopeUser == nil {
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		return nil, restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
	}
	return &identity, nil
}

func messageReadsError(c echo.Context, err error) error {
	var invalid *dto.MessageReadUUIDError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.Is(err, domain.ErrMessageReadNotFound):
		return c.JSON(404, map[string]string{"msg": "message not found."})
	case errors.Is(err, domain.ErrMessageReadForbidden):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(c.Request())})
	default:
		return restError(c, err)
	}
}

func messageReadsPermissionDetail(request *http.Request) string {
	if restLanguage(request) == "en" {
		return "You do not have permission to perform this action."
	}
	return "Non hai l'autorizzazione per eseguire questa azione."
}
