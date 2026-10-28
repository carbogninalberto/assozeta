package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CarnetListService interface {
	List(context.Context, domain.ResolvedIdentity, string) (any, error)
}

type CarnetInfoService interface {
	Info(context.Context, domain.ResolvedIdentity, string) (map[string]any, error)
}

type CarnetListHandler struct {
	lists      CarnetListService
	infos      CarnetInfoService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCarnetListHandler(lists CarnetListService, infos CarnetInfoService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CarnetListHandler {
	return &CarnetListHandler{lists: lists, infos: infos, actors: actors, identities: identities}
}

func (h *CarnetListHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "carnet_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Carnet List")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.lists.List(request.Context(), *identity, request.Header.Get("X-Group-Id"))
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, body)
}

func (h *CarnetListHandler) Info(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "carnet_info")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Carnet Info", "description": "Info about a carnet",
			"renders": []string{"application/json"},
			"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.infos.Info(request.Context(), *identity, c.Param("uid"))
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

type CarnetSubscriptionListService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, group string) (any, error)
}

type CarnetSubscriptionListHandler struct {
	lists      CarnetSubscriptionListService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCarnetSubscriptionListHandler(lists CarnetSubscriptionListService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *CarnetSubscriptionListHandler {
	return &CarnetSubscriptionListHandler{lists: lists, actors: actors, identities: identities}
}

func (h *CarnetSubscriptionListHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "carnet_subscription_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Subscription List", "Set carnet subscription to disabled")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.lists.List(request.Context(), *identity, parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id"))
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetSubscriptionListSubscription):
		return c.JSON(404, map[string]string{"msg": "Subscription not found."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}
