package handler

import (
	"errors"
	"mime"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type MessageTemplatesHandler struct {
	templates  *service.MessageTemplates
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewMessageTemplatesHandler(templates *service.MessageTemplates, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *MessageTemplatesHandler {
	return &MessageTemplatesHandler{templates: templates, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *MessageTemplatesHandler) Register(e *echo.Echo, audit echo.MiddlewareFunc) {
	e.Any("/communications/settings/message-templates", h.serve("message_template_list", "GET, POST", ""), audit)
	e.Any("/communications/settings/message-templates/sample", h.serve("message_template_sample", "POST", ""), audit)
	e.Any("/communications/settings/message-templates/:template_id", h.serve("message_template_detail", "PATCH, DELETE", "template_id"), audit)
	e.Any("/communications/outgoing/recipients/:associate_id", h.serve("outgoing_message_recipient", "GET, POST", "associate_id"), audit)
	e.Any("/communications/outgoing/previews/:preview_id", h.serve("outgoing_message_action", "POST", "preview_id"), audit)
}

func (h *MessageTemplatesHandler) serve(operation, methods, parameter string) echo.HandlerFunc {
	return func(c echo.Context) error {
		identity, err := messageMutationsIdentity(c, h.actors, h.identities, methods, operation)
		if identity == nil {
			return err
		}
		r := c.Request()
		if err = h.templates.Admit(r.Context(), *identity, strings.HasPrefix(operation, "outgoing_")); err != nil {
			return restError(c, err)
		}
		if r.Method == http.MethodOptions {
			return c.JSON(200, map[string]any{
				"name": strings.Title(strings.ReplaceAll(operation, "_", " ")), "description": "",
				"renders": []string{"application/json"}, "parses": []string{"application/json"},
			})
		}
		if !strings.Contains(", "+methods+", ", ", "+r.Method+", ") {
			return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
		}
		id := ""
		group := r.Header.Get("X-Group-Id")
		if parameter != "" {
			id, err = dto.MessageTemplateID(c.Param(parameter))
			if err != nil {
				return messageTemplateError(c, err)
			}
		}
		if r.Method == http.MethodGet {
			if operation == "message_template_list" {
				rows, err := h.templates.List(r.Context(), *identity)
				if err != nil {
					return messageTemplateError(c, err)
				}
				return c.JSON(200, map[string]any{"data": rows, "variables": dto.MessageTemplateVariables})
			}
			rows, err := h.templates.Options(r.Context(), *identity, id, group)
			return messageTemplateResponse(c, 200, rows, err)
		}
		if r.Method != http.MethodDelete && r.ContentLength != 0 {
			media, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
			if media != "application/json" {
				return restError(c, &restInputError{Status: 415, Detail: restText(r, "media")})
			}
		}
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		if r.Method == http.MethodDelete {
			if err := h.templates.Archive(write.Context, *identity, id); err != nil {
				return messageTemplateError(c, err)
			}
			return c.NoContent(204)
		}
		data, ok := write.Body.(domain.RequestObject)
		if write.Body != nil && !ok {
			return c.JSON(400, map[string]string{"detail": "Invia un oggetto JSON."})
		}
		if write.Body == nil && r.ContentLength != 0 {
			return c.JSON(400, map[string]string{"detail": "Invia un oggetto JSON."})
		}
		switch operation {
		case "message_template_sample":
			result, err := h.templates.Sample(write.Context, *identity, data)
			return messageTemplateResponse(c, 200, result, err)
		case "outgoing_message_recipient":
			result, err := h.templates.Preview(write.Context, *identity, id, group, data)
			return messageTemplateResponse(c, 201, result, err)
		case "outgoing_message_action":
			result, err := h.templates.Action(write.Context, *identity, id, group, data, write.Audit)
			return messageTemplateResponse(c, 200, result, err)
		default:
			status := 200
			if r.Method == http.MethodPost {
				status = 201
			}
			result, err := h.templates.Save(write.Context, *identity, id, data)
			return messageTemplateResponse(c, status, result, err)
		}
	}
}

func messageTemplateResponse(c echo.Context, status int, data any, err error) error {
	if err != nil {
		return messageTemplateError(c, err)
	}
	return c.JSON(status, map[string]any{"data": data})
}

func messageTemplateError(c echo.Context, err error) error {
	var invalid *dto.MessageTemplateError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, map[string]string{invalid.Field: invalid.Detail})
	case errors.Is(err, domain.ErrMessageTemplateNotFound):
		return restError(c, &restInputError{Status: 404, Detail: restText(c.Request(), "notfound")})
	case errors.Is(err, service.ErrOutgoingMessageUsed), errors.Is(err, service.ErrOutgoingRecipientChanged):
		return c.JSON(409, map[string]string{"detail": err.Error()})
	default:
		return restError(c, err)
	}
}
