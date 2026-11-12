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

type FolderWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, body service.FolderBody) (dto.FolderNode, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body service.FolderBody) (dto.FolderNode, error)
}

// FolderWritesHandler serves FolderViewSet.as_view({'post': 'add'}) and
// as_view({'patch': 'update'}); the viewset's own delete() also answers DELETE
// on both (no pk on add: a 500; on update it deletes the folder).
type FolderWritesHandler struct {
	writes     FolderWriteService
	deletes    FolderDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	operations *paymentcategorylifecycle.Lifecycle
}

func NewFolderWritesHandler(writes FolderWriteService, deletes FolderDeleteService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) *FolderWritesHandler {
	return &FolderWritesHandler{writes: writes, deletes: deletes, actors: actors, identities: identities, operations: operations}
}

func (h *FolderWritesHandler) Add(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "folders_add")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return folderMoveOptions(c)
	case http.MethodDelete:
		// get_object() without a pk.
		return restError(c, errors.New("folder delete without pk"))
	case http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return h.write(c, func(ctx context.Context, body service.FolderBody) (int, dto.FolderNode, error) {
		folder, err := h.writes.Add(ctx, *identity, body)
		return http.StatusCreated, folder, err
	})
}

func (h *FolderWritesHandler) Update(c echo.Context) error {
	c.Response().Header().Set("Allow", "PATCH, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "folders_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	pk := c.Param("pk")
	switch request.Method {
	case http.MethodOptions:
		return messageReadsOptions(c, "Folder")
	case http.MethodPatch, http.MethodDelete:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return h.write(c, func(ctx context.Context, body service.FolderBody) (int, dto.FolderNode, error) {
		if request.Method == http.MethodDelete {
			return http.StatusNoContent, dto.FolderNode{}, h.deletes.Delete(ctx, *identity, pk)
		}
		folder, err := h.writes.Update(ctx, *identity, pk, body)
		return http.StatusOK, folder, err
	})
}

// write tracks and admits the operation; the body is read up front but its
// parse error is returned by the service where Django first touches
// request.data (after the association and object lookups).
func (h *FolderWritesHandler) write(c echo.Context, run func(context.Context, service.FolderBody) (int, dto.FolderNode, error)) error {
	request := c.Request()
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body service.FolderBody
	if request.Method != http.MethodDelete {
		body = folderRequestBody(c)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	status, folder, err := run(ctx, body)
	var invalid *service.FolderValidationError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, folderValidationMessages(request, invalid.Fields))
	case errors.Is(err, domain.ErrFolderForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, service.ErrFolderLookup):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrFolderNotFound):
		return restError(c, &restInputError{Status: 404, Detail: err.Error()})
	case err != nil:
		return restError(c, err)
	}
	if status == http.StatusNoContent {
		return c.NoContent(status)
	}
	return c.JSON(status, folder)
}

// folderRequestBody is request.data as the view sees it: a form body, or a
// multipart body without files, is an immutable QueryDict; a multipart body
// with files is a mutable copy; JSON is mutable only as a dict.
func folderRequestBody(c echo.Context) service.FolderBody {
	value, err := readMessageMutationsBody(c)
	media, _, _ := mime.ParseMediaType(c.Request().Header.Get("Content-Type"))
	body := service.FolderBody{Value: value, Err: err}
	switch media {
	case "application/x-www-form-urlencoded":
		body.Form = true
	case "multipart/form-data":
		body.Form = true
		object, _ := value.(domain.RequestObject)
		for _, field := range object {
			if _, upload := field.Value.(domain.RequestUpload); upload {
				body.Mutable = true
			}
		}
	default:
		_, body.Mutable = value.(domain.RequestObject)
	}
	return body
}

func folderValidationMessages(request *http.Request, fields map[string][]string) any {
	english := restLanguage(request) == "en"
	result := make(map[string]any, len(fields))
	for field, codes := range fields {
		messages := make([]string, 0, len(codes))
		for _, code := range codes {
			switch {
			case code == dto.FolderValidationUnique && english:
				messages = append(messages, "The fields name, parent, sport_association must make a unique set.")
			case code == dto.FolderValidationUnique:
				messages = append(messages, "I campi name, parent, sport_association devono costituire un insieme unico.")
			default:
				messages = append(messages, paymentCategoryValidationMessage(code, english))
			}
		}
		result[field] = messages
	}
	return result
}
