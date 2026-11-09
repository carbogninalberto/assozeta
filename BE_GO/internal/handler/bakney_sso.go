package handler

import (
	"context"
	"errors"
	"math"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// BakneyThrottleStore is the Django cache holding DRF throttle histories.
type BakneyThrottleStore interface {
	History(context.Context, string) ([]float64, error)
	Store(context.Context, string, []float64, time.Duration) error
}

// BakneyAdministrators is is_instance_administrator on the authenticated actor.
type BakneyAdministrators interface {
	Allowed(context.Context, domain.Account) (bool, error)
}

// BakneySSOHandler serves BE/instance/sso/views.py: PairingAdminView
// (instance/admin/bakney-pairing) and the public bakney/v1 views. Every view
// is a SecureView: DRF's initial() (content negotiation, authentication and
// PairingAdministrator for the admin view, HandoffThrottle 30/min per view
// class and client), then the view inside one transaction, and the
// Cache-Control/Pragma/Referrer-Policy/X-Frame-Options headers on every DRF
// response.
type BakneySSOHandler struct {
	sso        *service.BakneySSO
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	admins     BakneyAdministrators
	throttle   BakneyThrottleStore
	audit      LoginAuditReader
	now        func() time.Time
}

func NewBakneySSOHandler(sso *service.BakneySSO, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	admins BakneyAdministrators, throttle BakneyThrottleStore, audit LoginAuditReader, now func() time.Time) *BakneySSOHandler {
	return &BakneySSOHandler{sso: sso, actors: actors, identities: identities, admins: admins, throttle: throttle, audit: audit, now: now}
}

const bakneyAdminDenied = "Only the instance owner or an administrator can administer Bakney pairing."

var bakneySecureHeaders = [][2]string{{"Cache-Control", "no-store"}, {"Pragma", "no-cache"}, {"Referrer-Policy", "no-referrer"},
	{"X-Frame-Options", "DENY"}}

func (h *BakneySSOHandler) secure(c echo.Context, allow string) {
	header := c.Response().Header()
	header.Set("Allow", allow)
	for _, item := range bakneySecureHeaders {
		header.Set(item[0], item[1])
	}
}

// fail answers an exception DRF does not handle: Django's error page,
// without the DRF response headers.
func (h *BakneySSOHandler) fail(c echo.Context, err error) error {
	header := c.Response().Header()
	for _, item := range bakneySecureHeaders {
		header.Del(item[0])
	}
	header.Del("Allow")
	_ = err // Never serialize provider errors or credentials into responses.
	return restError(c, errBakneySSOServer)
}

var errBakneySSOServer = errors.New("bakney sso server error")

// initial is APIView.initial for the public views (no authentication,
// AllowAny): content negotiation, then the throttle.
func (h *BakneySSOHandler) initial(c echo.Context, view, allow string) (bool, error) {
	h.secure(c, allow)
	request := c.Request()
	c.Response().Header().Set("Content-Language", restLanguage(request))
	query := parseReferenceForm(request.URL.RawQuery)
	if format, present := query.Lookup("format"); present && format != "" && format != "json" {
		return false, restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	}
	if !acceptsJSON(request.Header.Get("Accept")) {
		return false, restError(c, &restInputError{Status: 406, Detail: restText(request, "accept")})
	}
	return h.allow(c, view)
}

// allow is HandoffThrottle.allow_request plus DRF's Throttled response.
func (h *BakneySSOHandler) allow(c echo.Context, view string) (bool, error) {
	request := c.Request()
	key := "throttle_bakney-sso:" + view + "_" + throttleIdent(request)
	history, err := h.throttle.History(request.Context(), key)
	if err != nil {
		return false, h.fail(c, err)
	}
	now := float64(h.now().UnixNano()) / 1e9
	for len(history) > 0 && history[len(history)-1] <= now-60 {
		history = history[:len(history)-1]
	}
	if len(history) >= 30 {
		detail := "Request was throttled."
		if restLanguage(request) != "en" {
			detail = "La richiesta è stata limitata (throttled)."
		}
		remaining := 60.0
		if len(history) > 0 {
			remaining = 60 - (now - history[len(history)-1])
		}
		if available := 30 - len(history) + 1; available > 0 {
			wait := int(math.Ceil(remaining / float64(available)))
			unit := "seconds"
			if wait == 1 {
				unit = "second"
			}
			detail += " Expected available in " + strconv.Itoa(wait) + " " + unit + "."
			c.Response().Header().Set("Retry-After", strconv.Itoa(wait))
		}
		return false, c.JSON(429, map[string]string{"detail": detail})
	}
	history = append([]float64{now}, history...)
	if err := h.throttle.Store(request.Context(), key, history, 60*time.Second); err != nil {
		return false, h.fail(c, err)
	}
	return true, nil
}

// throttleIdent is BaseThrottle.get_ident (NUM_PROXIES unset).
func throttleIdent(request *http.Request) string {
	if forwarded := strings.Join(request.Header.Values("X-Forwarded-For"), ","); forwarded != "" {
		return strings.Join(strings.FieldsFunc(forwarded, func(r rune) bool { return isPythonSpace(r) }), "")
	}
	host, _, err := net.SplitHostPort(request.RemoteAddr)
	if err != nil {
		return request.RemoteAddr
	}
	return host
}

func isPythonSpace(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, 0x85, 0xa0:
		return true
	}
	return r > 0xff && strings.ContainsRune("                　", r)
}

func (h *BakneySSOHandler) notAllowed(c echo.Context) error {
	return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
}

// answer writes a view result: an SSOError as {'error': code}, a request.data
// error as DRF's response, anything else as the error page.
func (h *BakneySSOHandler) answer(c echo.Context, reply *service.BakneySSOReply, err error) error {
	var ssoErr *service.BakneySSOError
	var bodyErr *service.BakneySSOBodyError
	switch {
	case errors.As(err, &ssoErr):
		return c.JSON(ssoErr.Status, map[string]string{"error": ssoErr.Code})
	case errors.As(err, &bodyErr):
		var input *restInputError
		if errors.As(bodyErr.Err, &input) {
			return restError(c, input)
		}
		return h.fail(c, bodyErr.Err)
	case err != nil:
		return h.fail(c, err)
	}
	header := c.Response().Header()
	for _, cookie := range reply.Cookies {
		header.Add("Set-Cookie", cookie)
	}
	if reply.Location != "" {
		header.Set("Location", reply.Location)
		header.Set("Content-Type", "text/html; charset=utf-8")
		c.Response().WriteHeader(reply.Status)
		return nil
	}
	return c.JSON(reply.Status, reply.JSON)
}

// requestData is request.data, read when the view first touches it.
func requestData(c echo.Context) func() (any, error) {
	return func() (any, error) { return readMessageMutationsBody(c) }
}

// Admin is PairingAdminView (GET, POST).
func (h *BakneySSOHandler) Admin(c echo.Context) error {
	h.secure(c, "GET, POST, HEAD, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "bakney_pairing_admin")
	if identity == nil {
		return err
	}
	allowed, err := h.admins.Allowed(c.Request().Context(), *identity.Actor)
	if err != nil {
		return h.fail(c, err)
	}
	if !allowed {
		return c.JSON(403, map[string]string{"detail": bakneyAdminDenied})
	}
	if ok, err := h.allow(c, "PairingAdminView"); !ok {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Pairing Admin", "")
	case http.MethodGet, http.MethodHead:
		reply, err := h.sso.AdminRead(request.Context())
		return h.answer(c, reply, err)
	case http.MethodPost:
		reply, err := h.sso.AdminWrite(request.Context(), requestData(c))
		return h.answer(c, reply, err)
	}
	return h.notAllowed(c)
}

func (h *BakneySSOHandler) handshake(c echo.Context, view, name string, commit bool) error {
	if ok, err := h.initial(c, view, "POST, OPTIONS"); !ok {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, "")
	case http.MethodPost:
		reply, err := h.sso.Handshake(request.Context(), commit, requestData(c))
		return h.answer(c, reply, err)
	}
	return h.notAllowed(c)
}

// Challenge is PairingChallengeView (POST).
func (h *BakneySSOHandler) Challenge(c echo.Context) error {
	return h.handshake(c, "PairingChallengeView", "Pairing Challenge", false)
}

// Commit is PairingCommitView (POST).
func (h *BakneySSOHandler) Commit(c echo.Context) error {
	return h.handshake(c, "PairingCommitView", "Pairing Commit", true)
}

// browser collects what the handoff views read from the request.
func (h *BakneySSOHandler) browser(c echo.Context) (service.BakneyBrowser, error) {
	request := c.Request()
	scheme := "http"
	if request.TLS != nil {
		scheme = "https"
	}
	if values, present := request.Header["X-Forwarded-Proto"]; present {
		first, _, _ := strings.Cut(strings.Join(values, ","), ",")
		scheme = "http"
		if strings.TrimFunc(first, isPythonSpace) == "https" {
			scheme = "https"
		}
	}
	host := request.Host
	if values, present := request.Header["X-Forwarded-Host"]; present {
		host = strings.Join(values, ",")
	}
	origin, present := request.Header["Origin"]
	browser := service.BakneyBrowser{Origin: scheme + "://" + host, Query: pythonQueryPairs(request.URL.RawQuery),
		Cookies: djangoRequestCookies(request), Authorization: strings.Join(request.Header.Values("Authorization"), ","),
		OriginPresent: present, OriginHeader: strings.Join(origin, ","), CSRF: strings.Join(request.Header.Values("X-SSO-CSRF"), ",")}
	if request.Method == http.MethodPost {
		audit, err := h.audit(request)
		if err != nil {
			return browser, err
		}
		browser.Audit = audit
	}
	return browser, nil
}

func (h *BakneySSOHandler) handoff(c echo.Context, view, name string, run func(context.Context, service.BakneyBrowser) (*service.BakneySSOReply, error)) error {
	if ok, err := h.initial(c, view, "GET, HEAD, OPTIONS"); !ok {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, "")
	case http.MethodGet, http.MethodHead:
		browser, err := h.browser(c)
		if err != nil {
			return h.fail(c, err)
		}
		reply, err := run(request.Context(), browser)
		return h.answer(c, reply, err)
	}
	return h.notAllowed(c)
}

// LoginStart is LoginStartView (GET).
func (h *BakneySSOHandler) LoginStart(c echo.Context) error {
	return h.handoff(c, "LoginStartView", "Login Start", h.sso.LoginStart)
}

// Callback is LoginCallbackView (GET).
func (h *BakneySSOHandler) Callback(c echo.Context) error {
	return h.handoff(c, "LoginCallbackView", "Login Callback", h.sso.Callback)
}

// Session is LoginSessionView (GET, POST).
func (h *BakneySSOHandler) Session(c echo.Context) error {
	if ok, err := h.initial(c, "LoginSessionView", "GET, POST, HEAD, OPTIONS"); !ok {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Login Session", "")
	}
	if request.Method != http.MethodGet && request.Method != http.MethodHead && request.Method != http.MethodPost {
		return h.notAllowed(c)
	}
	browser, err := h.browser(c)
	if err != nil {
		return h.fail(c, err)
	}
	if request.Method == http.MethodPost {
		reply, err := h.sso.SessionLogin(request.Context(), browser, requestData(c))
		return h.answer(c, reply, err)
	}
	reply, err := h.sso.SessionRead(request.Context(), browser)
	return h.answer(c, reply, err)
}

// djangoRequestCookies is request.COOKIES (django.http.cookie.parse_cookie).
func djangoRequestCookies(request *http.Request) map[string]string {
	cookies := map[string]string{}
	header := strings.Join(request.Header.Values("Cookie"), ",")
	for _, chunk := range strings.Split(header, ";") {
		key, value, found := strings.Cut(chunk, "=")
		if !found {
			key, value = "", chunk
		}
		key, value = strings.TrimFunc(key, isPythonSpace), strings.TrimFunc(value, isPythonSpace)
		if key != "" || value != "" {
			cookies[key] = unquoteDjangoCookie(value)
		}
	}
	return cookies
}

// unquoteDjangoCookie is http.cookies._unquote.
func unquoteDjangoCookie(value string) string {
	if len(value) < 2 || value[0] != '"' || value[len(value)-1] != '"' {
		return value
	}
	inner := value[1 : len(value)-1]
	var out strings.Builder
	for i := 0; i < len(inner); i++ {
		if inner[i] != '\\' || i+1 >= len(inner) {
			out.WriteByte(inner[i])
			continue
		}
		octal := func(b byte) bool { return b >= '0' && b <= '7' }
		if i+3 < len(inner) && octal(inner[i+1]) && octal(inner[i+2]) && octal(inner[i+3]) && inner[i+1] <= '3' {
			out.WriteByte((inner[i+1]-'0')<<6 | (inner[i+2]-'0')<<3 | (inner[i+3] - '0'))
			i += 3
			continue
		}
		out.WriteByte(inner[i+1])
		i++
	}
	return out.String()
}

// pythonQueryPairs is QueryDict(QUERY_STRING) as ordered pairs: parse_qsl
// with keep_blank_values and unquote_plus.
func pythonQueryPairs(raw string) [][2]string {
	var pairs [][2]string
	for _, chunk := range strings.Split(raw, "&") {
		if chunk == "" {
			continue
		}
		key, value, _ := strings.Cut(chunk, "=")
		pairs = append(pairs, [2]string{pythonUnquotePlus(key), pythonUnquotePlus(value)})
	}
	return pairs
}
