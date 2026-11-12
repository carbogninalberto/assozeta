package handler

import (
	"errors"
	"net/http"
	"net/url"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// PersonasWritesHandler serves AssociateViewSet's add, update, delete,
// recover and bulk_delete routes, the DELETE of its other pk routes, and
// AssociateSubscriptionViewSet's personas-subscriptions/<pk>/list.
type PersonasWritesHandler struct {
	personas      *service.Personas
	writes        *service.PersonaWrites
	subscriptions *service.PersonaSubscriptions
	actors        *service.ActorAuthentication
	identities    *service.IdentityResolver
	audit         LoginAuditReader
	operations    *paymentcategorylifecycle.Lifecycle
}

func NewPersonasWritesHandler(personas *service.Personas, writes *service.PersonaWrites, subscriptions *service.PersonaSubscriptions,
	actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader,
	operations *paymentcategorylifecycle.Lifecycle) *PersonasWritesHandler {
	return &PersonasWritesHandler{personas: personas, writes: writes, subscriptions: subscriptions, actors: actors, identities: identities,
		audit: audit, operations: operations}
}

// WithWrites routes DELETE on personas/<pk>/info through the soft delete.
func (h *PersonasHandler) WithWrites(writes *PersonasWritesHandler) *PersonasHandler {
	h.writes = writes
	return h
}

func personaPathKey(c echo.Context) string {
	pk, err := url.PathUnescape(c.Param("pk"))
	if err != nil {
		return c.Param("pk")
	}
	return pk
}

// personaWriteMessages renders serializer.errors.
func personaWriteMessages(request *http.Request, invalid *dto.PersonaInvalid) map[string][]string {
	english := restLanguage(request) == "en"
	result := make(map[string][]string, len(invalid.Fields))
	for name, codes := range invalid.Fields {
		messages := make([]string, len(codes))
		for i, code := range codes {
			switch {
			case code == dto.PersonaDateFormat && english:
				messages[i] = "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
			case code == dto.PersonaDateFormat:
				messages[i] = "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
			default:
				messages[i] = paymentCategoryValidationMessage(code, english)
			}
		}
		result[name] = messages
	}
	return result
}

// personaWriteError maps the viewset's exceptions.
func personaWriteError(c echo.Context, err error) error {
	request := c.Request()
	var invalid *dto.PersonaInvalid
	var picture *service.PersonaPictureInvalid
	switch {
	case errors.As(err, &picture):
		return c.JSON(400, map[string]string{"picture_path": picture.Text})
	case errors.As(err, &invalid):
		return c.JSON(400, personaWriteMessages(request, invalid))
	case errors.Is(err, service.ErrPersonaNotSportAssociation):
		return restError(c, &domain.PermissionDenied{Detail: "User is not a sport association"})
	case errors.Is(err, domain.ErrPersonaWriteDenied):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case errors.Is(err, domain.ErrPersonaWriteForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, service.ErrPersonaInvalidPK):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrPersonaNotFound):
		return c.JSON(404, map[string]string{"detail": "No Associate matches the given query."})
	case errors.Is(err, domain.ErrPersonaSubscriptionNotFound):
		return c.JSON(404, map[string]string{"detail": "No Subscription matches the given query."})
	case errors.Is(err, domain.ErrPersonaRecoverMissing):
		return c.NoContent(404)
	case errors.Is(err, dto.ErrDRFInvalidPage):
		return c.JSON(404, map[string]string{"detail": "Invalid page."})
	}
	return messageReadsError(c, err)
}

// personaOptionsPost is SimpleMetadata's actions.POST over AssociateSerializer.
func personaOptionsPost() map[string]any {
	meta := func(kind, name string, readOnly, required bool, extra map[string]any) map[string]any {
		info := map[string]any{"type": kind, "required": required, "read_only": readOnly}
		if name != "" {
			info["label"] = strings.ToUpper(name[:1]) + strings.ReplaceAll(name[1:], "_", " ")
		}
		for key, value := range extra {
			info[key] = value
		}
		return info
	}
	sex := []map[string]string{{"value": "A", "display_name": "other"}, {"value": "M", "display_name": "male"},
		{"value": "F", "display_name": "female"}}
	model := func(writableKeys bool) map[string]any {
		fields := map[string]any{"associate_id": meta("string", "associate_id", !writableKeys, false, nil),
			"sex":           meta("choice", "sex", false, false, map[string]any{"choices": sex}),
			"born_date":     meta("date", "born_date", false, false, nil),
			"creation_date": meta("datetime", "creation_date", !writableKeys, false, nil)}
		for _, field := range dto.PersonaTextFields {
			kind, extra := "string", map[string]any{}
			if field.Email {
				kind = "email"
			}
			if field.Max > 0 {
				extra["max_length"] = field.Max
			}
			fields[field.Name] = meta(kind, field.Name, false, false, extra)
		}
		for _, name := range dto.PersonaBooleanFields {
			fields[name] = meta("boolean", name, false, false, nil)
		}
		for _, name := range dto.PersonaRelationFields {
			fields[name] = meta("field", name, false, false, nil)
		}
		return fields
	}
	nested := func(name string, children map[string]any) map[string]any {
		return meta("field", name, true, false, map[string]any{"child": map[string]any{"type": "nested object", "required": false,
			"read_only": true, "children": children}})
	}
	basicTutors := model(true)
	basicTutors["full_name"] = meta("string", "full_name", true, false, nil)
	basicTutors["tutors"] = meta("field", "tutors", true, false, nil)
	basic := model(true)
	basic["full_name"] = meta("string", "full_name", true, false, nil)
	basic["tutors"] = nested("tutors", basicTutors)
	tutor := meta("nested object", "tutor", true, false, map[string]any{"children": basic})
	post := model(false)
	post["full_name"] = meta("string", "full_name", true, false, nil)
	post["is_minor_now"] = meta("boolean", "is_minor_now", true, false, nil)
	post["is_tutor"] = meta("boolean", "is_tutor", true, false, nil)
	post["age"] = meta("integer", "age", true, false, nil)
	post["family_members"] = meta("field", "family_members", true, false, nil)
	post["tutors"] = nested("tutors", map[string]any{"tutor": tutor, "is_primary": meta("boolean", "is_primary", false, false, nil)})
	post["tutors_data"] = meta("list", "tutors_data", false, false, map[string]any{"child": map[string]any{"type": "field",
		"required": true, "read_only": false}})
	post["incomplete"] = meta("boolean", "incomplete", true, false, nil)
	return map[string]any{"POST": post}
}

func personaOptions(c echo.Context, actions bool) error {
	body := map[string]any{"name": "Associate", "description": "", "renders": []string{"application/json"},
		"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}}
	if actions {
		body["actions"] = personaOptionsPost()
	}
	return c.JSON(200, body)
}

// begin resolves the identity for one route of the viewset (Allow set first).
func (h *PersonasWritesHandler) begin(c echo.Context, allow, method string) (*domain.ResolvedIdentity, error) {
	c.Response().Header().Set("Allow", allow)
	return messageMutationsIdentity(c, h.actors, h.identities, method, "AssociateViewSet")
}

// softDelete is the class's delete() with a pk: get_object(), then deleted=True.
func (h *PersonasWritesHandler) softDelete(c echo.Context, identity domain.ResolvedIdentity, pk string) error {
	request := c.Request()
	query, header := parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id")
	row, err := h.writes.Locate(request.Context(), identity, pk, query, header)
	if err != nil {
		return personaWriteError(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.writes.Delete(write.Context, identity, row, header, write.Audit); err != nil {
		return personaWriteError(c, err)
	}
	return c.NoContent(204)
}

// deleteWithoutPK is the class's delete() on a route without pk:
// get_queryset, then get_object's lookup-kwarg assertion.
func (h *PersonasWritesHandler) deleteWithoutPK(c echo.Context, identity domain.ResolvedIdentity) error {
	request := c.Request()
	err := h.personas.Delete(request.Context(), identity, nil, parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id"))
	return personaWriteError(c, err)
}

func (h *PersonasWritesHandler) Add(c echo.Context) error {
	identity, err := h.begin(c, "POST, DELETE, OPTIONS", http.MethodPost)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return personaOptions(c, true)
	case http.MethodDelete:
		return h.deleteWithoutPK(c, *identity)
	case http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	// request.user.sport_association runs before request.data is parsed.
	association, err := h.writes.Association(request.Context(), *identity)
	if err != nil {
		return personaWriteError(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	header := request.Header.Get("X-Group-Id")
	id, err := h.writes.Add(write.Context, *identity, association, write.Body, write.Form, header, write.Audit)
	if err != nil {
		return personaWriteError(c, err)
	}
	body, err := h.personas.Render(write.Context, id, header)
	if err != nil {
		return personaWriteError(c, err)
	}
	body = service.PersonaSentGroup(body, header)
	return c.Blob(201, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}

func (h *PersonasWritesHandler) Update(c echo.Context) error {
	identity, err := h.begin(c, "PATCH, DELETE, OPTIONS", http.MethodPatch)
	if identity == nil {
		return err
	}
	request := c.Request()
	pk := personaPathKey(c)
	switch request.Method {
	case http.MethodOptions:
		return personaOptions(c, false)
	case http.MethodDelete:
		return h.softDelete(c, *identity, pk)
	case http.MethodPatch:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	query, header := parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id")
	row, err := h.writes.Locate(request.Context(), *identity, pk, query, header)
	if err != nil {
		return personaWriteError(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.writes.Update(write.Context, *identity, row, write.Body, write.Form, header, write.Audit); err != nil {
		return personaWriteError(c, err)
	}
	// The refreshed object: get_object() again.
	body, err := h.personas.Info(write.Context, *identity, pk, query, header)
	if err != nil {
		return personaWriteError(c, err)
	}
	return c.Blob(200, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}

func (h *PersonasWritesHandler) Delete(c echo.Context) error {
	identity, err := h.begin(c, "DELETE, OPTIONS", http.MethodDelete)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return personaOptions(c, false)
	case http.MethodDelete:
		return h.softDelete(c, *identity, personaPathKey(c))
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

func (h *PersonasWritesHandler) Recover(c echo.Context) error {
	identity, err := h.begin(c, "POST, DELETE, OPTIONS", http.MethodPost)
	if identity == nil {
		return err
	}
	request := c.Request()
	pk := personaPathKey(c)
	switch request.Method {
	case http.MethodOptions:
		return personaOptions(c, true)
	case http.MethodDelete:
		return h.softDelete(c, *identity, pk)
	case http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.writes.Recover(write.Context, *identity, pk, request.Header.Get("X-Group-Id"), write.Audit); err != nil {
		return personaWriteError(c, err)
	}
	return c.JSON(200, map[string]string{"mgs": "Ripristinata con successo"})
}

func (h *PersonasWritesHandler) BulkDelete(c echo.Context) error {
	identity, err := h.begin(c, "DELETE, OPTIONS", http.MethodDelete)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return personaOptions(c, false)
	case http.MethodDelete:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.writes.BulkDelete(write.Context, *identity, write.Body, request.Header.Get("X-Group-Id")); err != nil {
		return personaWriteError(c, err)
	}
	return c.NoContent(204)
}

// Subscriptions serves personas-subscriptions/<pk>/list (GET list; the GET
// mapping adds HEAD and the class's delete() answers DELETE).
func (h *PersonasWritesHandler) Subscriptions(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, HEAD, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "AssociateSubscriptionViewSet")
	if identity == nil {
		return err
	}
	request := c.Request()
	pk, header := personaPathKey(c), request.Header.Get("X-Group-Id")
	switch request.Method {
	case http.MethodOptions:
		return c.JSON(200, map[string]any{"name": "Associate Subscription", "description": "", "renders": []string{"application/json"},
			"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	case http.MethodDelete:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		if err = h.subscriptions.Delete(write.Context, *identity, pk, header, write.Audit); err != nil {
			return personaWriteError(c, err)
		}
		return c.NoContent(204)
	case http.MethodGet, http.MethodHead:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.subscriptions.List(request.Context(), *identity, pk, parseReferenceForm(request.URL.RawQuery), header)
	if err != nil {
		return personaWriteError(c, err)
	}
	return c.Blob(200, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}
