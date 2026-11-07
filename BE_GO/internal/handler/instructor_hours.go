package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InstructorHoursService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, raw string, parameters domain.RequestObject, audit domain.LoginAuditContext) (map[string]any, error)
	Add(ctx context.Context, identity domain.ResolvedIdentity, raw string, load service.InstructorHoursBody, group string, audit domain.LoginAuditContext) error
	Update(ctx context.Context, identity domain.ResolvedIdentity, instructor, hours string, load service.InstructorHoursBody, group string,
		audit domain.LoginAuditContext) error
	Delete(ctx context.Context, identity domain.ResolvedIdentity, instructor, hours string, audit domain.LoginAuditContext) error
	Calculate(ctx context.Context, identity domain.ResolvedIdentity, raw string, load service.InstructorHoursBody, group string) (map[string]any, error)
}

type InstructorHoursHandler struct {
	hours      InstructorHoursService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInstructorHoursHandler(hours InstructorHoursService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InstructorHoursHandler {
	return &InstructorHoursHandler{hours: hours, actors: actors, identities: identities, audit: audit, operations: operations}
}

// instructorHoursRequest is one admitted request; Body reads request.data on
// first use (the views read it after their lookups) and keeps a parse error
// for the response.
type instructorHoursRequest struct {
	c         echo.Context
	identity  domain.ResolvedIdentity
	audit     domain.LoginAuditContext
	bodyError error
}

func (r *instructorHoursRequest) Body() (any, bool, error) {
	body, err := readMessageMutationsBody(r.c)
	if err != nil {
		r.bodyError = err
		return nil, false, err
	}
	media, _, _ := mime.ParseMediaType(r.c.Request().Header.Get("Content-Type"))
	return body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data", nil
}

func instructorHoursMessages(request *http.Request, fields map[string]any) any {
	english := restLanguage(request) == "en"
	translated := make(map[string]any, len(fields))
	for name, value := range fields {
		if codes, ok := value.([]string); ok {
			messages := make([]string, len(codes))
			for index, code := range codes {
				messages[index] = code
				switch code {
				case dto.SubscriptionMembershipDate:
					messages[index] = "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
					if english {
						messages[index] = "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
					}
				case dto.InstructorHoursJSON:
					messages[index] = "Il valore deve essere un JSON valido."
					if english {
						messages[index] = "Value must be valid JSON."
					}
				}
			}
			value = messages
		}
		translated[name] = value
	}
	return paymentCategoryValidationMessages(request, translated)
}

func (h *InstructorHoursHandler) failure(c echo.Context, request *instructorHoursRequest, err error) error {
	httpRequest := c.Request()
	var invalid *service.InstructorHoursValidationError
	var answer *service.InstructorHoursAnswer
	switch {
	case request != nil && request.bodyError != nil && errors.Is(err, request.bodyError):
		return restError(c, err)
	case errors.As(err, &invalid):
		return c.JSON(400, instructorHoursMessages(httpRequest, invalid.Fields))
	case errors.As(err, &answer):
		return c.JSON(answer.Status, answer.Body)
	case errors.Is(err, service.ErrInstructorHoursForbidden):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(httpRequest)})
	case errors.Is(err, service.ErrInstructorHoursNotFound):
		return c.JSON(404, map[string]string{"error": "hours not found"})
	case errors.Is(err, service.ErrInstructorNotFound):
		return c.JSON(404, map[string]string{"error": "instructor not found"})
	case errors.Is(err, dto.ErrDRFInvalidPage):
		detail := "Invalid page."
		if restLanguage(httpRequest) != "en" {
			detail = "Pagina non valida."
		}
		return c.JSON(404, map[string]string{"detail": detail})
	}
	// Malformed uids (400 list) and the refused, not ported rows (500 page).
	return messageReadsError(c, err)
}

// serve is @api_view([method]): identity and permissions, OPTIONS, 405, then
// the admitted request without reading its body.
func (h *InstructorHoursHandler) serve(c echo.Context, method, operation, name string,
	run func(ctx context.Context, request *instructorHoursRequest) error) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	httpRequest := c.Request()
	if httpRequest.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if httpRequest.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(httpRequest, "method")})
	}
	tracked, err := h.operations.Track(httpRequest.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	auditContext, err := h.audit(httpRequest)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	request := &instructorHoursRequest{c: c, identity: *identity, audit: auditContext}
	if err := run(ctx, request); err != nil {
		return h.failure(c, request, err)
	}
	return nil
}

func (h *InstructorHoursHandler) List(c echo.Context) error {
	return h.serve(c, http.MethodGet, "instructor_hours_list", "Instructor Hours List", func(ctx context.Context, r *instructorHoursRequest) error {
		body, err := h.hours.List(ctx, r.identity, c.Param("uid"), parseReferenceForm(c.Request().URL.RawQuery), r.audit)
		if err != nil {
			return err
		}
		return c.JSON(200, body)
	})
}

func (h *InstructorHoursHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "instructor_hours_add", "Instructor Hours Add", func(ctx context.Context, r *instructorHoursRequest) error {
		if err := h.hours.Add(ctx, r.identity, c.Param("uid"), r.Body, c.Request().Header.Get("X-Group-Id"), r.audit); err != nil {
			return err
		}
		return c.JSON(200, map[string]string{"msg": "hours added."})
	})
}

func (h *InstructorHoursHandler) Update(c echo.Context) error {
	return h.serve(c, http.MethodPatch, "instructor_hours_update", "Instructor Hours Update", func(ctx context.Context, r *instructorHoursRequest) error {
		if err := h.hours.Update(ctx, r.identity, c.Param("uid"), c.Param("instructor_hours_id"), r.Body, c.Request().Header.Get("X-Group-Id"),
			r.audit); err != nil {
			return err
		}
		return c.JSON(200, map[string]bool{"success": true})
	})
}

func (h *InstructorHoursHandler) Delete(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "instructor_hours_delete", "Instructor Hours Delete", func(ctx context.Context, r *instructorHoursRequest) error {
		if err := h.hours.Delete(ctx, r.identity, c.Param("uid"), c.Param("instructor_hours_id"), r.audit); err != nil {
			return err
		}
		return c.JSON(200, map[string]string{"msg": "hours deleted."})
	})
}

func (h *InstructorHoursHandler) Calculate(c echo.Context) error {
	return h.serve(c, http.MethodPost, "instructor_hours_calculate", "Instructor Hours Calculate", func(ctx context.Context, r *instructorHoursRequest) error {
		body, err := h.hours.Calculate(ctx, r.identity, c.Param("uid"), r.Body, c.Request().Header.Get("X-Group-Id"))
		if err != nil {
			return err
		}
		return c.JSON(200, body)
	})
}
