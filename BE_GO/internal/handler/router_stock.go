package handler

import (
	"errors"
	"net/http"
	"regexp"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// routerStockAction serves one bound viewset action; pk is empty on the list
// and extra-action routes.
type routerStockAction func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error

// routerStockViewset is one router.register() of BE/application/urls.py with
// the handlers DefaultRouter binds. A nil action is a handler whose
// signature rejects the call (TypeError: 500); formatted lists the actions
// whose handler accepts the format= keyword of the format-suffix routes.
type routerStockViewset struct {
	prefix, name, description string
	parses                    []string
	list, create, listDelete  routerStockAction
	retrieve, update, destroy routerStockAction
	partialUpdate             routerStockAction
	bulkDelete                routerStockAction
	bulkName                  string
	formatted                 map[string]bool
	// postFields is SimpleMetadata's POST description (the list route);
	// putFields runs get_object() first: (nil, nil) when it raises an
	// APIException or Http404, an error for anything else (500).
	postFields func(c echo.Context, identity *domain.ResolvedIdentity) (map[string]any, error)
	putFields  func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error)
}

// errRouterStockSignature is the TypeError of a handler called with a
// keyword its signature does not accept (partial=, format=) or a
// get_object() without a lookup key.
var errRouterStockSignature = errors.New("router action called with arguments its handler does not accept")

var routerStockParses = []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}

const routerStockLookup = `[^/.]+`
const routerStockFormat = `(?:\.([a-z0-9]+)/?|/)`

type routerStockRoute struct {
	pattern  *regexp.Regexp
	kind     string
	viewset  *routerStockViewset
	hasPK    bool
	isFormat bool
}

// RouterStockHandler is DefaultRouter's url patterns in registration order:
// per viewset the list route, its extra actions, the detail route (each with
// its format twin), then the API root.
type RouterStockHandler struct {
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	unmatched  echo.HandlerFunc
	viewsets   []*routerStockViewset
	routes     []routerStockRoute
}

func NewRouterStockHandler(actors *service.ActorAuthentication, identities *service.IdentityResolver, unmatched echo.HandlerFunc) *RouterStockHandler {
	return &RouterStockHandler{actors: actors, identities: identities, unmatched: unmatched}
}

// Register adds a viewset in router.register() order.
func (h *RouterStockHandler) register(viewset *routerStockViewset) {
	if viewset.parses == nil {
		viewset.parses = routerStockParses
	}
	h.viewsets = append(h.viewsets, viewset)
	prefix := regexp.QuoteMeta(viewset.prefix)
	h.routes = append(h.routes, routerStockRoute{pattern: regexp.MustCompile(`^/` + prefix + routerStockFormat + `$`), kind: "list", viewset: viewset})
	if viewset.bulkDelete != nil {
		h.routes = append(h.routes, routerStockRoute{pattern: regexp.MustCompile(`^/` + prefix + `/bulk_delete` + routerStockFormat + `$`),
			kind: "bulk", viewset: viewset})
	}
	h.routes = append(h.routes, routerStockRoute{pattern: regexp.MustCompile(`^/` + prefix + `/(` + routerStockLookup + `)` + routerStockFormat + `$`),
		kind: "detail", viewset: viewset, hasPK: true})
}

// Routes returns the Echo paths that reach Serve; Serve answers anything else
// under them as an unmatched URL.
func (h *RouterStockHandler) Routes() []string {
	paths := []string{"/", "/.*"}
	for _, viewset := range h.viewsets {
		paths = append(paths, "/"+viewset.prefix+"/", "/"+viewset.prefix+"/*", "/"+viewset.prefix+".*")
	}
	return paths
}

var routerStockRoot = regexp.MustCompile(`^/(?:\.([a-z0-9]+)/?)?$`)

func (h *RouterStockHandler) Serve(c echo.Context) error {
	path := c.Request().URL.Path
	for _, route := range h.routes {
		match := route.pattern.FindStringSubmatch(path)
		if match == nil {
			continue
		}
		pk, format := "", match[len(match)-1]
		if route.hasPK {
			pk = match[1]
		}
		return h.serveViewset(c, route, pk, format)
	}
	if match := routerStockRoot.FindStringSubmatch(path); match != nil {
		return h.serveRoot(c, match[1])
	}
	if writer, ok := c.Get(djangoPageLocaleKey).(*djangoPageLocaleWriter); ok {
		writer.enabled = true
	}
	return h.unmatched(c)
}

var routerStockAllow = map[string]string{
	"list":   "GET, POST, DELETE, HEAD, OPTIONS",
	"detail": "GET, PUT, PATCH, DELETE, HEAD, OPTIONS",
	"bulk":   "DELETE, OPTIONS",
	"root":   "GET, HEAD, OPTIONS",
}

// routerStockNegotiate is APIView.initial's content negotiation, before
// authentication: a format suffix (or ?format=) no renderer serves is 404.
func routerStockNegotiate(c echo.Context, format string) error {
	request := c.Request()
	c.Response().Header().Set("Content-Language", restLanguage(request))
	if format != "" && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	}
	return nil
}

func (h *RouterStockHandler) serveViewset(c echo.Context, route routerStockRoute, pk, format string) error {
	viewset := route.viewset
	c.Response().Header().Set("Allow", routerStockAllow[route.kind])
	if format != "" {
		if err := routerStockNegotiate(c, format); err != nil || c.Response().Committed {
			return err
		}
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "router_stock")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return h.options(c, identity, route, pk)
	}
	var action routerStockAction
	var name string
	switch route.kind {
	case "list":
		switch request.Method {
		case http.MethodGet, http.MethodHead:
			action, name = viewset.list, "list"
		case http.MethodPost:
			action, name = viewset.create, "create"
		case http.MethodDelete:
			action, name = viewset.listDelete, "delete"
		}
	case "bulk":
		if request.Method == http.MethodDelete {
			action, name = viewset.bulkDelete, "bulk_delete"
		}
	case "detail":
		switch request.Method {
		case http.MethodGet, http.MethodHead:
			action, name = viewset.retrieve, "retrieve"
		case http.MethodPut:
			action, name = viewset.update, "update"
		case http.MethodPatch:
			action, name = viewset.partialUpdate, "partial_update"
		case http.MethodDelete:
			action, name = viewset.destroy, "destroy"
		}
	}
	if name == "" {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if action == nil || (format != "" && !viewset.formatted[name]) {
		return messageReadsError(c, errRouterStockSignature)
	}
	return action(c, identity, pk)
}

func (h *RouterStockHandler) options(c echo.Context, identity *domain.ResolvedIdentity, route routerStockRoute, pk string) error {
	viewset := route.viewset
	name, description := viewset.name+" List", viewset.description
	var actions map[string]any
	switch route.kind {
	case "list":
		if viewset.postFields != nil {
			fields, err := viewset.postFields(c, identity)
			if err != nil {
				return messageReadsError(c, err)
			}
			if fields != nil {
				actions = map[string]any{"POST": fields}
			}
		}
	case "detail":
		name = viewset.name + " Instance"
		if viewset.putFields != nil {
			fields, err := viewset.putFields(c, identity, pk)
			if err != nil {
				return messageReadsError(c, err)
			}
			if fields != nil {
				actions = map[string]any{"PUT": fields}
			}
		}
	case "bulk":
		name, description = viewset.bulkName, ""
	}
	body := map[string]any{"name": name, "description": description, "renders": []string{"application/json"}, "parses": viewset.parses}
	if actions != nil {
		body["actions"] = actions
	}
	return c.JSON(200, body)
}

// serveRoot is DefaultRouter's APIRootView (AllowAny): each prefix reversed
// by its '<basename>-list' name, which resolves to the last pattern with that
// name accepting the arguments (with a format suffix only the router's format
// routes do), as an absolute URL on the request's host.
func (h *RouterStockHandler) serveRoot(c echo.Context, format string) error {
	c.Response().Header().Set("Allow", routerStockAllow["root"])
	if format != "" {
		if err := routerStockNegotiate(c, format); err != nil || c.Response().Committed {
			return err
		}
	}
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, "router_stock_root", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return c.JSON(200, map[string]any{"name": "Api Root", "description": "The default basic root view for DefaultRouter",
			"renders": []string{"application/json"}, "parses": routerStockParses})
	case http.MethodGet, http.MethodHead:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	// USE_X_FORWARDED_HOST and SECURE_PROXY_SSL_HEADER (core/settings.py).
	host := request.Header.Get("X-Forwarded-Host")
	if host == "" {
		host = request.Host
	}
	scheme := "http"
	if proto, _, _ := strings.Cut(request.Header.Get("X-Forwarded-Proto"), ","); strings.TrimSpace(proto) == "https" {
		scheme = "https"
	}
	body := map[string]string{}
	for _, viewset := range h.viewsets {
		target := "/" + viewset.prefix + "/"
		if format != "" {
			target = "/" + viewset.prefix + "." + format
		} else if explicit, ok := routerStockNamedLists[viewset.prefix]; ok {
			target = explicit
		}
		body[viewset.prefix] = scheme + "://" + host + target
	}
	return c.JSON(200, body)
}

// routerStockNamedLists are the explicit routes of BE/application/urls.py
// named like a router list route ('<basename>-list') without arguments; they
// follow include(router.urls), so reverse() returns them.
var routerStockNamedLists = map[string]string{
	"course-locations":         "/course/locations/list",
	"folders":                  "/folders/list",
	"subscription/memberships": "/subscription/memberships/list",
	"course-subscriptions":     "/course-subscriptions/list",
	"personas":                 "/personas/list",
}
