package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"sort"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SubscriptionMiscHandler struct {
	misc       *service.SubscriptionMisc
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionMiscHandler(misc *service.SubscriptionMisc, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionMiscHandler {
	return &SubscriptionMiscHandler{misc: misc, actors: actors, identities: identities, audit: audit, operations: operations}
}

// failure maps the views' responses; anything else is the 500 page.
func (h *SubscriptionMiscHandler) failure(c echo.Context, err error) error {
	var invalid *service.PaymentInfoUUIDError
	var denied *domain.PermissionDenied
	message := func(key, text string) error { return c.JSON(http.StatusBadRequest, map[string]string{key: text}) }
	switch {
	case errors.As(err, &invalid):
		return c.JSON(http.StatusBadRequest, []string{invalid.Detail}) // is_valid_uuid
	case errors.As(err, &denied):
		return restError(c, denied)
	case errors.Is(err, service.ErrSubscriptionMiscMissingData):
		return message("msg", "alcuni dati sono mancanti.")
	case errors.Is(err, service.ErrSubscriptionMiscInvalidData):
		return message("msg", "alcuni dati non sono validi.")
	case errors.Is(err, service.ErrSubscriptionMiscUnmapped):
		return message("msg", "Città non mappata.")
	case errors.Is(err, service.ErrSubscriptionMiscGymRequired):
		return message("error", "gym_name is required")
	}
	return messageReadsError(c, err)
}

// subscriptionMiscPlain turns rendered values into parsed-JSON values
// (RequestObject, []any, json.Number, float64, string, bool, nil) so floats
// keep Python's repr.
func subscriptionMiscPlain(value any) (any, error) {
	switch v := value.(type) {
	case nil, bool, string, float64, json.Number:
		return v, nil
	case json.RawMessage:
		return dto.ParseProfileTablesJSON(v)
	case int:
		return json.Number(strconv.Itoa(v)), nil
	case domain.RequestObject:
		result := make(domain.RequestObject, 0, len(v))
		for _, field := range v {
			item, err := subscriptionMiscPlain(field.Value)
			if err != nil {
				return nil, err
			}
			result = append(result, domain.RequestField{Name: field.Name, Value: item})
		}
		return result, nil
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		result := make(domain.RequestObject, 0, len(v))
		for _, key := range keys {
			item, err := subscriptionMiscPlain(v[key])
			if err != nil {
				return nil, err
			}
			result = append(result, domain.RequestField{Name: key, Value: item})
		}
		return result, nil
	case []any:
		result := make([]any, 0, len(v))
		for _, element := range v {
			item, err := subscriptionMiscPlain(element)
			if err != nil {
				return nil, err
			}
			result = append(result, item)
		}
		return result, nil
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return dto.ParseProfileTablesJSON(encoded)
}

func (h *SubscriptionMiscHandler) respond(c echo.Context, status int, value any) error {
	plain, err := subscriptionMiscPlain(value)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.Blob(status, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(plain))
}

// serve runs one @api_view function: identity and the registry (anonymous
// for the views without an authentication requirement), OPTIONS, the method
// check, then run.
func (h *SubscriptionMiscHandler) serve(c echo.Context, method, operation, name string, anonymous bool,
	run func(identity domain.ResolvedIdentity) error) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, method, operation, anonymous)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if request.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return run(*identity)
}

func (h *SubscriptionMiscHandler) admitted(c echo.Context, run func(write *restWrite) error) error {
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	return run(write)
}

func (h *SubscriptionMiscHandler) TaxCode(c echo.Context) error {
	return h.serve(c, http.MethodPost, "subscription_calculate_tax_code", "Subscription Calculate Tax Code", true,
		func(domain.ResolvedIdentity) error {
			return h.admitted(c, func(write *restWrite) error {
				result, err := h.misc.TaxCode(write.Body)
				if err != nil {
					return h.failure(c, err)
				}
				return h.respond(c, http.StatusOK, result)
			})
		})
}

func (h *SubscriptionMiscHandler) ImportStatus(c echo.Context) error {
	return h.serve(c, http.MethodGet, "subscription_import_status", "Subscription Import Status", false,
		func(identity domain.ResolvedIdentity) error {
			result, err := h.misc.ImportStatus(c.Request().Context(), identity)
			if err != nil {
				return h.failure(c, err)
			}
			return h.respond(c, http.StatusOK, result)
		})
}

func (h *SubscriptionMiscHandler) Federation(c echo.Context) error {
	return h.serve(c, http.MethodGet, "get_associations_for_federation", "Get Associations For Federation", false,
		func(identity domain.ResolvedIdentity) error {
			result, err := h.misc.Federation(c.Request().Context(), identity, c.Request().Header.Get("X-Group-Id"))
			if err != nil {
				return h.failure(c, err)
			}
			return h.respond(c, http.StatusOK, result)
		})
}

func (h *SubscriptionMiscHandler) GenerateLink(c echo.Context) error {
	// The response echoes the whole uploaded gym_name file (its lines), so this
	// one route asks the body reader to keep every byte of a file part.
	c.SetRequest(c.Request().WithContext(withWholeUploads(c.Request().Context())))
	return h.serve(c, http.MethodPost, "subscription_generate_token_link", "Subscription Generate Token Link", false,
		func(identity domain.ResolvedIdentity) error {
			return h.admitted(c, func(write *restWrite) error {
				result, err := h.misc.GenerateLink(write.Context, identity, write.Body, write.Audit)
				if err != nil {
					return h.failure(c, err)
				}
				return h.respond(c, http.StatusCreated, result)
			})
		})
}

// wholeUploadsKey marks a request whose file parts the body reader keeps in
// full: DRF's JSON encoder iterates an uploaded file object, so the views that
// echo one need every line, not just the first 64 KiB chunk.
type wholeUploadsKey struct{}

// withWholeUploads asks the multipart body reader to keep the whole content of
// every file part (Content = all bytes, Whole = true, Size unchanged).
func withWholeUploads(ctx context.Context) context.Context {
	return context.WithValue(ctx, wholeUploadsKey{}, true)
}

// wholeUploads reports whether the request keeps whole file parts.
func wholeUploads(ctx context.Context) bool {
	keep, _ := ctx.Value(wholeUploadsKey{}).(bool)
	return keep
}

func (h *SubscriptionMiscHandler) ValidateLink(c echo.Context) error {
	return h.serve(c, http.MethodGet, "validate_token_link_and_get_subscriptions", "Validate Token Link And Get Subscriptions", true,
		func(identity domain.ResolvedIdentity) error {
			return h.admitted(c, func(write *restWrite) error {
				request := c.Request()
				result, err := h.misc.ValidateLink(write.Context, identity, parseReferenceForm(request.URL.RawQuery),
					request.Header.Get("X-Group-Id"), write.Audit)
				if err != nil {
					return h.failure(c, err)
				}
				return h.respond(c, http.StatusOK, result)
			})
		})
}

func (h *SubscriptionMiscHandler) DeleteDocument(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "subscription_delete_document", "Subscription Delete Document", false,
		func(identity domain.ResolvedIdentity) error {
			return h.admitted(c, func(write *restWrite) error {
				err := h.misc.DeleteDocument(write.Context, identity, subscriptionReadsUID(c), subscriptionMiscFileID(c),
					c.Request().Header.Get("X-Group-Id"), write.Audit)
				if err != nil {
					return h.failure(c, err)
				}
				return h.respond(c, http.StatusOK, domain.RequestObject{{Name: "msg", Value: "subscription file deleted"}})
			})
		})
}

func subscriptionMiscFileID(c echo.Context) string {
	// The str converter receives the decoded segment.
	id, err := url.PathUnescape(c.Param("subscription_file_id"))
	if err != nil {
		return c.Param("subscription_file_id")
	}
	return id
}
