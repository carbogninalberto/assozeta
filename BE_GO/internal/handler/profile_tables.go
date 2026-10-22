package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type ProfileTablesWriter interface {
	Update(context.Context, domain.ResolvedIdentity, dto.ProfileTablesRequest, domain.LoginAuditContext) error
}
type ProfileTablesHandler struct {
	writer     ProfileTablesWriter
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
}

func NewProfileTablesHandler(writer ProfileTablesWriter, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader) *ProfileTablesHandler {
	return &ProfileTablesHandler{writer, actors, identities, audit}
}
func (h *ProfileTablesHandler) Update(c echo.Context) error {
	identity, err := authenticatedRESTIdentityBody(c, h.actors, h.identities, http.MethodPost, "profile_settings_tables", readProfileTablesBody)
	if identity == nil {
		return err
	}
	r := c.Request()
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Profile Settings Tables", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	body, err := readProfileTablesBody(c)
	if err != nil {
		return restError(c, err)
	}
	media, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	input, err := dto.ParseProfileTables(body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data")
	if err != nil {
		var validation *dto.ProfileTablesValidationError
		if !errors.As(err, &validation) {
			return restError(c, err)
		}
		field, message := "tables_settings", "Value must be valid JSON."
		italian := restLanguage(r) == "it"
		if italian {
			message = "Il valore deve essere un JSON valido."
		}
		switch validation.Code {
		case "object":
			field = "non_field_errors"
			message = "Invalid data. Expected a dictionary, but got " + validation.InputType + "."
			if italian {
				message = "Dati non validi. Era atteso un dizionario, ma si è ricevuto " + validation.InputType + "."
			}
		case "null-body":
			field = "non_field_errors"
			message = "No data provided"
		case "null":
			message = "This field may not be null."
			if italian {
				message = "Il campo non può essere nullo."
			}
		}
		return c.JSON(400, map[string][]string{field: {message}})
	}
	audit, err := h.audit(r)
	if err != nil {
		return restError(c, err)
	}
	if err = h.writer.Update(r.Context(), *identity, input, audit); err != nil {
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"status": "success"})
}

func readProfileTablesBody(c echo.Context) (any, error) {
	return readAuthenticatedRESTBodyUsing(c, func(r *http.Request) (any, error) {
		return readReferenceBodyJSON(r, dto.ParseProfileTablesJSON)
	})
}
