package handler

import (
	"context"
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

type StaffBoardService interface {
	Admit(context.Context, domain.ResolvedIdentity) error
	List(context.Context, domain.ResolvedIdentity) ([]dto.StaffBoardMessageRow, error)
	Add(context.Context, domain.ResolvedIdentity, any, bool) (dto.StaffBoardMessageRow, error)
	Update(context.Context, domain.ResolvedIdentity, string, any, bool) (dto.StaffBoardMessageRow, error)
	Delete(context.Context, domain.ResolvedIdentity, string) error
}

type StaffBoardHandler struct {
	board      StaffBoardService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	operations *paymentcategorylifecycle.Lifecycle
}

func NewStaffBoardHandler(board StaffBoardService, actors *service.ActorAuthentication, identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) *StaffBoardHandler {
	return &StaffBoardHandler{board: board, actors: actors, identities: identities, operations: operations}
}

// DRF view metadata measured from the installed views (name from the function,
// description from its docstring).
var staffBoardViews = map[string]struct{ method, name, description string }{
	"staff_board_messages_list": {http.MethodGet, "Staff Board Messages List",
		"Internal staff board: list all messages of the association, pinned first.\nVisible to admins and collaborators with association.communication.messages.read."},
	"staff_board_messages_add":    {http.MethodPost, "Staff Board Messages Add", ""},
	"staff_board_messages_update": {http.MethodPatch, "Staff Board Messages Update", ""},
	"staff_board_messages_delete": {http.MethodDelete, "Staff Board Messages Delete", ""},
}

func (h *StaffBoardHandler) List(c echo.Context) error {
	return h.serve(c, "staff_board_messages_list")
}
func (h *StaffBoardHandler) Add(c echo.Context) error { return h.serve(c, "staff_board_messages_add") }
func (h *StaffBoardHandler) Update(c echo.Context) error {
	return h.serve(c, "staff_board_messages_update")
}
func (h *StaffBoardHandler) Delete(c echo.Context) error {
	return h.serve(c, "staff_board_messages_delete")
}

func (h *StaffBoardHandler) serve(c echo.Context, operation string) error {
	view := staffBoardViews[operation]
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, view.method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if err = h.board.Admit(request.Context(), *identity); err != nil {
		return staffBoardError(c, err)
	}
	if request.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{
			"name": view.name, "description": view.description,
			"renders": []string{"application/json"},
			"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"},
		})
	}
	if request.Method != view.method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if view.method == http.MethodGet {
		rows, err := h.board.List(request.Context(), *identity)
		if err != nil {
			return staffBoardError(c, err)
		}
		return c.JSON(200, map[string]any{"data": rows})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	if view.method != http.MethodDelete {
		if body, err = readMessageMutationsBody(c); err != nil {
			return restError(c, err)
		}
	}
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	form := media == "application/x-www-form-urlencoded" || media == "multipart/form-data"
	operationContext, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	id := c.Param("staff_board_message_id")
	switch view.method {
	case http.MethodPost:
		row, err := h.board.Add(operationContext, *identity, body, form)
		if err != nil {
			return staffBoardError(c, err)
		}
		return c.JSON(201, map[string]any{"data": row})
	case http.MethodPatch:
		row, err := h.board.Update(operationContext, *identity, id, body, form)
		if err != nil {
			return staffBoardError(c, err)
		}
		return c.JSON(200, map[string]any{"data": row})
	default:
		if err = h.board.Delete(operationContext, *identity, id); err != nil {
			return staffBoardError(c, err)
		}
		return c.JSON(200, map[string]string{"msg": "message deleted."})
	}
}

func staffBoardError(c echo.Context, err error) error {
	var invalid *dto.StaffBoardInputError
	switch {
	case errors.As(err, &invalid):
		english := restLanguage(c.Request()) == "en"
		body := make(map[string][]string, len(invalid.Fields))
		for _, field := range invalid.Fields {
			for _, code := range field.Codes {
				body[field.Name] = append(body[field.Name], staffBoardValidationMessage(code, english))
			}
		}
		return c.JSON(400, body)
	case errors.Is(err, domain.ErrStaffBoardNotFound):
		return c.JSON(404, map[string]string{"error": "message not found."})
	case errors.Is(err, domain.ErrStaffBoardEditNotAuthor), errors.Is(err, domain.ErrStaffBoardDeleteNotAuthor):
		return c.JSON(403, map[string]string{"error": err.Error()})
	case errors.Is(err, domain.ErrStaffBoardForbidden):
		return messageReadsError(c, domain.ErrMessageReadForbidden)
	default:
		return messageReadsError(c, err)
	}
}

func staffBoardValidationMessage(code string, english bool) string {
	switch {
	case strings.HasPrefix(code, "text:"):
		return strings.TrimPrefix(code, "text:")
	case code == "json":
		if english {
			return "Value must be valid JSON."
		}
		return "Il valore deve essere un JSON valido."
	}
	return paymentCategoryValidationMessage(code, english)
}
