package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SubscriptionTagService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error)
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (map[string]any, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	Link(ctx context.Context, identity domain.ResolvedIdentity, tag, subscription, group string, linked bool, audit domain.LoginAuditContext) error
}

type SubscriptionTagsHandler struct {
	tags       SubscriptionTagService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionTagsHandler(tags SubscriptionTagService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionTagsHandler {
	return &SubscriptionTagsHandler{tags: tags, actors: actors, identities: identities, audit: audit, operations: operations}
}

const (
	subscriptionTagsNameDoc = ":param request: tag_name\n:return: list of tags"
	subscriptionTagsLinkDoc = "API endpoint to update a tag for the subscriptions\n:param request: tag_id, subscription_id\n:return: list of tags"
)

// subscriptionTagDetail is str() of rest_framework's ValidationError(text):
// the repr of its detail list, which the views return as the message.
func subscriptionTagDetail(text string) string {
	return "[ErrorDetail(string='" + text + "', code='invalid')]"
}

// List follows subscription_tags_list.
func (h *SubscriptionTagsHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "subscription_tags_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Subscription Tags List", "API endpoint to get the list of tags for the subscriptions\n:param request: None\n:return: list of tags")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.tags.List(request.Context(), *identity)
	var denied *domain.PermissionDenied
	if errors.As(err, &denied) {
		return restError(c, denied)
	}
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

// serve runs one write view; failure maps a TagService ValidationError to
// the view's response.
func (h *SubscriptionTagsHandler) serve(c echo.Context, method, operation, name, description string,
	failure func(err error) (int, map[string]string, bool),
	run func(write *restWrite, identity domain.ResolvedIdentity) (any, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	if request.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	result, err := run(write, *identity)
	var denied *domain.PermissionDenied
	if errors.As(err, &denied) {
		return restError(c, denied)
	}
	if err != nil {
		if status, body, ok := failure(err); ok {
			return c.JSON(status, body)
		}
		return messageReadsError(c, err)
	}
	return c.JSON(200, result)
}

// subscriptionTagNotFound maps the ValidationErrors of update (400 for the name, 404
// otherwise), delete, assign and unassign to {'msg': str(e)}.
func subscriptionTagNotFound(err error) (int, map[string]string, bool) {
	switch {
	case errors.Is(err, service.ErrSubscriptionTagNameRequired):
		return 400, map[string]string{"msg": subscriptionTagDetail("tag_name is required")}, true
	case errors.Is(err, domain.ErrSubscriptionTagNotFound):
		return 404, map[string]string{"msg": subscriptionTagDetail("tag not found")}, true
	case errors.Is(err, domain.ErrSubscriptionTagSubscriptionNotFound):
		return 404, map[string]string{"msg": subscriptionTagDetail("subscription not found")}, true
	}
	return 0, nil, false
}

// Add follows subscription_tags_add: every ValidationError is 400 {'error': str(e)}.
func (h *SubscriptionTagsHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "subscription_tags_add", "Subscription Tags Add",
		"API endpoint to add a new tag for the subscriptions\n"+subscriptionTagsNameDoc,
		func(err error) (int, map[string]string, bool) {
			if errors.Is(err, service.ErrSubscriptionTagNameRequired) {
				return 400, map[string]string{"error": subscriptionTagDetail("tag_name is required")}, true
			}
			return 0, nil, false
		},
		func(write *restWrite, identity domain.ResolvedIdentity) (any, error) {
			return h.tags.Add(write.Context, identity, write.Body, write.Audit)
		})
}

func (h *SubscriptionTagsHandler) Update(c echo.Context) error {
	tag := c.Param("tag_id")
	return h.serve(c, http.MethodPatch, "subscription_tags_update", "Subscription Tags Update",
		"API endpoint to update a tag for the subscriptions\n"+subscriptionTagsNameDoc, subscriptionTagNotFound,
		func(write *restWrite, identity domain.ResolvedIdentity) (any, error) {
			return h.tags.Update(write.Context, identity, tag, write.Body, write.Audit)
		})
}

func (h *SubscriptionTagsHandler) Delete(c echo.Context) error {
	tag := c.Param("tag_id")
	return h.serve(c, http.MethodDelete, "subscription_tags_delete", "Subscription Tags Delete",
		"API endpoint to delete a tag for the subscriptions\n"+subscriptionTagsNameDoc, subscriptionTagNotFound,
		func(write *restWrite, identity domain.ResolvedIdentity) (any, error) {
			if err := h.tags.Delete(write.Context, identity, tag, write.Audit); err != nil {
				return nil, err
			}
			return map[string]string{"msg": "tag deleted"}, nil
		})
}

func (h *SubscriptionTagsHandler) link(c echo.Context, operation, name, message string, linked bool) error {
	tag, subscription := c.Param("tag_id"), c.Param("subscription_id")
	return h.serve(c, http.MethodPatch, operation, name, subscriptionTagsLinkDoc, subscriptionTagNotFound,
		func(write *restWrite, identity domain.ResolvedIdentity) (any, error) {
			if err := h.tags.Link(write.Context, identity, tag, subscription, c.Request().Header.Get("X-Group-Id"), linked, write.Audit); err != nil {
				return nil, err
			}
			return map[string]string{"msg": message}, nil
		})
}

func (h *SubscriptionTagsHandler) Assign(c echo.Context) error {
	return h.link(c, "subscription_tags_assign", "Subscription Tags Assign", "tag assigned", true)
}

func (h *SubscriptionTagsHandler) Unassign(c echo.Context) error {
	return h.link(c, "subscription_tags_unassign", "Subscription Tags Unassign", "tag unassigned", false)
}
