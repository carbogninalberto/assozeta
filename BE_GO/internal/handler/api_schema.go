package handler

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/apischema"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// APISchemaHandler serves schema/ (SpectacularAPIView), schema/swagger-ui/
// (SpectacularSwaggerView) and schema/redoc/ (SpectacularRedocView) with
// SPECTACULAR_SETTINGS: SERVE_PUBLIC, AllowAny, the project's JWT
// authentication and the sidecar UI assets. The documents are generated from
// Django (see package apischema); this handler reproduces the request
// handling around them in DRF's order: content negotiation, authentication and
// identity, method dispatch, then the view.
type APISchemaHandler struct {
	actors           *service.ActorAuthentication
	identities       *service.IdentityResolver
	csrfCookieSecure bool
}

func NewAPISchemaHandler(actors *service.ActorAuthentication, identities *service.IdentityResolver, csrfCookieSecure bool) *APISchemaHandler {
	return &APISchemaHandler{actors: actors, identities: identities, csrfCookieSecure: csrfCookieSecure}
}

type apiSchemaView struct {
	operation string // the view class name, as the scoped request check sees it
	renderers []apischema.Renderer
}

var (
	apiSchemaDocumentView = apiSchemaView{"SpectacularAPIView", apischema.SchemaRenderers}
	apiSchemaSwaggerView  = apiSchemaView{"SpectacularSwaggerView", apischema.PageRenderers}
	apiSchemaRedocView    = apiSchemaView{"SpectacularRedocView", apischema.PageRenderers}
	// Query values this port forwards into documents and pages verbatim. Other
	// `lang`/`version` values are refused rather than approximated.
	apiSchemaLanguagePattern = regexp.MustCompile(`^[A-Za-z0-9_.@:-]+$`)
	apiSchemaVersionPattern  = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
	errAPISchemaUnsupported  = errors.New("api schema: request outside the ported input range")
)

const apiSchemaPath = "/schema/"

// Schema serves SpectacularAPIView.
func (h *APISchemaHandler) Schema(c echo.Context) error {
	return h.serve(c, apiSchemaDocumentView, func(request apiSchemaRequest) error {
		if request.options {
			body, ok := apischema.Options(request.format(), request.language)
			if !ok {
				return apiSchemaRefuse(c)
			}
			return request.respond(http.StatusOK, request.reindent(body), "")
		}
		key, ok := apischema.CatalogKey(request.language)
		if lang := request.query("lang"); lang != "" {
			if !apiSchemaLanguagePattern.MatchString(lang) {
				return apiSchemaRefuse(c)
			}
			key, ok = apischema.CatalogKey(lang)
		}
		version := request.query("version")
		if !ok || (version != "" && !apiSchemaVersionPattern.MatchString(version)) {
			return apiSchemaRefuse(c)
		}
		document, ok := apischema.Document(request.format(), key, version)
		if !ok {
			return apiSchemaRefuse(c)
		}
		suffix := ""
		if version != "" {
			suffix = " (" + version + ")"
		}
		c.Response().Header().Set("Content-Disposition",
			fmt.Sprintf(`inline; filename="%s%s.%s"`, apischema.Title(), suffix, request.renderer.Format))
		return request.respond(http.StatusOK, request.reindent(document), "")
	})
}

// Swagger serves SpectacularSwaggerView.
func (h *APISchemaHandler) Swagger(c echo.Context) error {
	return h.serve(c, apiSchemaSwaggerView, func(request apiSchemaRequest) error {
		if request.options {
			// The OPTIONS metadata renders the page template without its
			// context; including an empty script template name fails.
			return apiSchemaServerError(c)
		}
		url, ok := apiSchemaURL(request)
		if !ok {
			return apiSchemaRefuse(c)
		}
		token := h.issueCSRFToken(c)
		page := strings.Replace(apischema.Page("swagger"), "{{schema_url}}", djangoEscapeJS(url), 1)
		page = strings.Replace(page, "{{csrf_token}}", token, 1)
		c.Response().Header().Set("Cross-Origin-Opener-Policy", "unsafe-none")
		return request.respond(http.StatusOK, page, "")
	})
}

// Redoc serves SpectacularRedocView.
func (h *APISchemaHandler) Redoc(c echo.Context) error {
	return h.serve(c, apiSchemaRedocView, func(request apiSchemaRequest) error {
		if request.options {
			return request.respond(http.StatusOK, apischema.Page("redoc_options"), "")
		}
		url, ok := apiSchemaURL(request)
		if !ok {
			return apiSchemaRefuse(c)
		}
		return request.respond(http.StatusOK, strings.Replace(apischema.Page("redoc"), "{{schema_url}}", djangoEscapeHTML(url), 1), "")
	})
}

// AppendSlash mirrors CommonMiddleware's APPEND_SLASH redirect for the three
// routes requested without their trailing slash (DEBUG is off, so every
// method is redirected).
func (h *APISchemaHandler) AppendSlash(c echo.Context) error {
	request := c.Request()
	location := request.URL.EscapedPath() + "/"
	if request.URL.RawQuery != "" {
		location += "?" + djangoIRIToURI(request.URL.RawQuery)
	}
	header := c.Response().Header()
	header.Set("Content-Type", "text/html; charset=utf-8")
	header.Set("Location", location)
	c.Response().WriteHeader(http.StatusMovedPermanently)
	return nil
}

type apiSchemaRequest struct {
	c        echo.Context
	values   map[string]string
	renderer apischema.Renderer
	media    string
	language string
	options  bool
}

func (r apiSchemaRequest) query(name string) string { return r.values[name] }

func (r apiSchemaRequest) format() apischema.Format {
	return apischema.Format(r.renderer.Format)
}

// reindent applies an Accept `indent` parameter to a JSON body generated with
// OpenApiJsonRenderer's default indent of 4.
func (r apiSchemaRequest) reindent(body string) string {
	if r.renderer.Format != "json" {
		return body
	}
	indent := apischema.JSONIndent(r.media)
	if indent == 4 {
		return body
	}
	var out bytes.Buffer
	if err := json.Indent(&out, []byte(body), "", strings.Repeat(" ", indent)); err != nil {
		panic("api schema: generated JSON does not parse: " + err.Error())
	}
	return out.String()
}

// respond writes a rendered DRF response: Content-Type is the renderer's media
// type plus its charset (the accepted media type only reaches render()), and
// CommonMiddleware sets Content-Length.
func (r apiSchemaRequest) respond(status int, body, contentType string) error {
	if contentType == "" {
		contentType = r.renderer.MediaType
		if r.renderer.Charset != "" {
			contentType += "; charset=" + r.renderer.Charset
		}
	}
	header := r.c.Response().Header()
	header.Set("Content-Type", contentType)
	header.Set("Content-Length", strconv.Itoa(len(body)))
	r.c.Response().WriteHeader(status)
	// net/http drops the body of a HEAD response after the encoding
	// middleware has seen it, as the ASGI server does after GZipMiddleware.
	_, err := r.c.Response().Write([]byte(body))
	return err
}

func (h *APISchemaHandler) serve(c echo.Context, view apiSchemaView, handle func(apiSchemaRequest) error) error {
	httpRequest := c.Request()
	header := c.Response().Header()
	// APIView.default_response_headers.
	header.Set("Allow", "GET, HEAD, OPTIONS")
	if len(view.renderers) > 1 {
		header.Set("Vary", "Accept")
	}
	values, ok := djangoQueryLast(httpRequest.URL.RawQuery)
	accept := strings.Join(httpRequest.Header.Values("Accept"), ",")
	_, acceptPresent := httpRequest.Header["Accept"]
	if !ok || !apischema.SupportedAccept(accept) {
		return apiSchemaRefuse(c)
	}
	request := apiSchemaRequest{c: c, values: values, language: handlerReadRouteLocale.Language(httpRequest)}
	negotiation := apischema.Negotiate(view.renderers, accept, acceptPresent, values["format"])
	if negotiation.Status != 0 {
		// finalize_response falls back to the first renderer.
		request.renderer, request.media = view.renderers[0], view.renderers[0].MediaType
		kind, code := readRouteAccept, "not_acceptable"
		if negotiation.Status == http.StatusNotFound {
			kind, code = readRouteNotFound, "not_found"
		}
		return request.error(negotiation.Status, apischema.Map{{Key: "detail", Value: apischema.ErrorDetail{
			String: handlerReadRouteLocale.Text(httpRequest, kind), Code: code}}})
	}
	request.renderer, request.media = view.renderers[negotiation.Renderer], negotiation.MediaType

	// ScopedJWTAuthentication: anonymous requests skip identity resolution.
	actor, err := h.actors.Authenticate(httpRequest.Context(), httpRequest.Header.Get("Authorization"))
	if err == nil && actor != nil {
		var body any
		var bodyErr error
		if httpRequest.Header.Get("X-Impersonation-Id") != "" || httpRequest.Header.Get("User-Id") != "" {
			body, bodyErr = readMessageMutationsBody(c)
		}
		_, err = h.identities.Resolve(httpRequest.Context(), actor, domain.IdentityRequest{
			Method: httpRequest.Method, Path: httpRequest.URL.Path, SessionID: httpRequest.Header.Get("X-Impersonation-Id"),
			TargetID: httpRequest.Header.Get("User-Id"), GroupID: httpRequest.Header.Get("X-Group-Id"),
			References: &domain.RequestReferences{Operation: view.operation, Path: routePathReferences(c),
				Query: parseReferenceForm(httpRequest.URL.RawQuery), Body: body, BodyError: bodyErr},
		})
	}
	var failure *domain.AuthenticationFailure
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &failure):
		header.Set("WWW-Authenticate", `Bearer realm="api"`)
		code := apischema.ErrorDetail{String: failure.Code, Code: failure.Code}
		data := apischema.Map{{Key: "detail", Value: apischema.ErrorDetail{String: failure.Detail, Code: failure.Code}}, {Key: "code", Value: code}}
		if failure.Code == "token_not_valid" && failure.Detail == "Given token not valid for any token type" {
			message := "Token is invalid"
			if failure.TokenExpired {
				message = "Token is expired"
			}
			detail := func(text string) apischema.ErrorDetail {
				return apischema.ErrorDetail{String: text, Code: failure.Code}
			}
			data = append(data, apischema.Pair{Key: "messages", Value: []apischema.Value{apischema.Map{
				{Key: "token_class", Value: detail("AccessToken")}, {Key: "token_type", Value: detail("access")},
				{Key: "message", Value: detail(message)}}}})
		}
		return request.error(http.StatusUnauthorized, data)
	case errors.As(err, &denied):
		data := apischema.Map{{Key: "detail", Value: apischema.ErrorDetail{String: denied.Detail, Code: "permission_denied"}}}
		if denied.ImpersonationInvalid {
			data = append(data, apischema.Pair{Key: "impersonation_invalid", Value: apischema.ErrorDetail{String: "True", Code: "permission_denied"}})
		}
		return request.error(http.StatusForbidden, data)
	case err != nil:
		return apiSchemaServerError(c)
	}

	switch httpRequest.Method {
	case http.MethodGet, http.MethodHead:
	case http.MethodOptions:
		request.options = true
	default:
		return request.error(http.StatusMethodNotAllowed, apischema.Map{{Key: "detail", Value: apischema.ErrorDetail{
			String: handlerReadRouteLocale.Text(httpRequest, readRouteMethod), Code: "method_not_allowed"}}})
	}
	return handle(request)
}

// error renders an exception response with the negotiated renderer.
func (r apiSchemaRequest) error(status int, data apischema.Map) error {
	switch r.renderer.Format {
	case "yaml":
		return r.respond(status, apischema.RenderYAML(data), "")
	case "json":
		return r.respond(status, apischema.RenderJSON(data, apischema.JSONIndent(r.media)), "")
	}
	// TemplateHTMLRenderer without an exception template.
	return r.respond(status, fmt.Sprintf("%d %s", status, http.StatusText(status)), "")
}

func apiSchemaServerError(c echo.Context) error {
	header := c.Response().Header()
	header.Del("Allow")
	header.Del("Vary")
	header.Del("Content-Disposition")
	header.Del("Cross-Origin-Opener-Policy")
	return restError(c, errAPISchemaUnsupported)
}

// apiSchemaRefuse answers requests outside the ported input range with the
// server error page instead of an approximated document.
func apiSchemaRefuse(c echo.Context) error {
	c.Logger().Warn(errAPISchemaUnsupported.Error())
	return apiSchemaServerError(c)
}

// apiSchemaURL is the views' _get_schema_url: DRF's reverse('schema') keeps
// the request's `format` (set_query_parameters then drops it when blank, and a
// page request only gets here with "html" or blank), followed by `lang` and
// `version` when sent, even empty, urlencoded.
func apiSchemaURL(request apiSchemaRequest) (string, bool) {
	url := apiSchemaPath
	var query []string
	if format := request.values["format"]; format != "" {
		query = append(query, "format="+pythonQuotePlus(format))
	}
	for _, name := range []string{"lang", "version"} {
		value, present := request.values[name]
		if !present {
			continue
		}
		pattern := apiSchemaVersionPattern
		if name == "lang" {
			pattern = apiSchemaLanguagePattern
		}
		if value != "" && !pattern.MatchString(value) {
			return "", false
		}
		query = append(query, name+"="+pythonQuotePlus(value))
	}
	if len(query) > 0 {
		url += "?" + strings.Join(query, "&")
	}
	return url, true
}

// pythonQuotePlus is urllib.parse.quote_plus for the ASCII values admitted above.
func pythonQuotePlus(value string) string {
	var out strings.Builder
	for i := 0; i < len(value); i++ {
		c := value[i]
		switch {
		case c >= 'A' && c <= 'Z', c >= 'a' && c <= 'z', c >= '0' && c <= '9', strings.IndexByte("_.-~", c) >= 0:
			out.WriteByte(c)
		case c == ' ':
			out.WriteByte('+')
		default:
			fmt.Fprintf(&out, "%%%02X", c)
		}
	}
	return out.String()
}

// djangoEscapeHTML is django.utils.html.escape.
func djangoEscapeHTML(value string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;").Replace(value)
}

// djangoEscapeJS is the escapejs filter.
func djangoEscapeJS(value string) string {
	var out strings.Builder
	for _, r := range value {
		switch {
		case strings.ContainsRune(`\'"><&=-;`+"`\u2028\u2029", r), r < 32:
			fmt.Fprintf(&out, `\u%04X`, r)
		default:
			out.WriteRune(r)
		}
	}
	return out.String()
}

// djangoIRIToURI is iri_to_uri applied to the latin-1 decoded QUERY_STRING.
func djangoIRIToURI(raw string) string {
	var out strings.Builder
	for i := 0; i < len(raw); i++ {
		c := raw[i]
		switch {
		case c >= 0x80:
			// The ASGI handler decodes the raw byte as latin-1; quote() then
			// percent-encodes that character's UTF-8 form.
			for _, b := range []byte(string(rune(c))) {
				fmt.Fprintf(&out, "%%%02X", b)
			}
		case c >= 'A' && c <= 'Z', c >= 'a' && c <= 'z', c >= '0' && c <= '9', strings.IndexByte("_.-~/#%[]=:;$&()+,!?*@'", c) >= 0:
			out.WriteByte(c)
		default:
			fmt.Fprintf(&out, "%%%02X", c)
		}
	}
	return out.String()
}

// djangoQueryLast parses QUERY_STRING as QueryDict does (parse_qsl with
// keep_blank_values) and returns the last value of each name, as
// QueryDict.get does. ok is false for a query of more than the 1000 fields
// DATA_UPLOAD_MAX_NUMBER_FIELDS admits.
func djangoQueryLast(raw string) (map[string]string, bool) {
	values := map[string]string{}
	pairs := strings.Split(raw, "&")
	fields := 0
	for _, pair := range pairs {
		if pair == "" {
			continue
		}
		fields++
		if fields > 1000 {
			return nil, false
		}
		name, value, _ := strings.Cut(pair, "=")
		values[pythonUnquotePlus(name)] = pythonUnquotePlus(value)
	}
	return values, true
}

// pythonUnquotePlus is urllib.parse.unquote_plus over the latin-1 decoded
// query: ASCII runs are percent-decoded as UTF-8 (invalid bytes replaced),
// other characters are kept.
func pythonUnquotePlus(value string) string {
	value = strings.ReplaceAll(value, "+", " ")
	var out strings.Builder
	var pending []byte
	flush := func() {
		for len(pending) > 0 {
			r, size := utf8.DecodeRune(pending)
			out.WriteRune(r)
			pending = pending[size:]
		}
	}
	for i := 0; i < len(value); i++ {
		c := value[i]
		if c >= 0x80 {
			flush()
			out.WriteRune(rune(c))
			continue
		}
		if c == '%' && i+2 < len(value) && isHex(value[i+1]) && isHex(value[i+2]) {
			decoded, _ := strconv.ParseUint(value[i+1:i+3], 16, 8)
			pending = append(pending, byte(decoded))
			i += 2
			continue
		}
		pending = append(pending, c)
	}
	flush()
	return out.String()
}

func isHex(c byte) bool {
	return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
}

const csrfAllowedChars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

// issueCSRFToken is the template's {{ csrf_token }}: get_token() masks the
// request's secret (CsrfViewMiddleware keeps a well-formed csrftoken cookie,
// unmasking a legacy 64-character one) or a new one, and the middleware then
// sets the cookie and Vary: Cookie.
func (h *APISchemaHandler) issueCSRFToken(c echo.Context) string {
	secret := ""
	if value, found := djangoRequestCookie(c.Request(), "csrftoken"); found && csrfWellFormed(value) {
		secret = value
		if len(value) == 64 {
			secret = csrfCipher(value[32:], value[:32], -1)
		}
	}
	if secret == "" {
		secret = csrfRandom()
	}
	mask := csrfRandom()
	cookie := fmt.Sprintf("csrftoken=%s; expires=%s; Max-Age=31449600; Path=/; SameSite=Lax", secret,
		time.Now().Add(31449600*time.Second).UTC().Format(http.TimeFormat))
	if h.csrfCookieSecure {
		cookie += "; Secure"
	}
	c.Response().Header().Add("Set-Cookie", cookie)
	varyAPISchema(c.Response().Header(), "Cookie")
	return mask + csrfCipher(secret, mask, 1)
}

func varyAPISchema(header http.Header, name string) {
	for _, value := range header.Values("Vary") {
		for _, token := range strings.Split(value, ",") {
			if strings.EqualFold(strings.TrimSpace(token), name) {
				return
			}
		}
	}
	if existing := header.Get("Vary"); existing != "" {
		header.Set("Vary", existing+", "+name)
		return
	}
	header.Set("Vary", name)
}

func csrfWellFormed(value string) bool {
	if len(value) != 32 && len(value) != 64 {
		return false
	}
	for i := 0; i < len(value); i++ {
		if strings.IndexByte(csrfAllowedChars, value[i]) < 0 {
			return false
		}
	}
	return true
}

// csrfCipher adds (sign 1) or subtracts (sign -1) the mask from the text,
// character by character, modulo the allowed alphabet.
func csrfCipher(text, mask string, sign int) string {
	out := make([]byte, len(text))
	n := len(csrfAllowedChars)
	for i := range text {
		x := strings.IndexByte(csrfAllowedChars, text[i])
		y := strings.IndexByte(csrfAllowedChars, mask[i])
		out[i] = csrfAllowedChars[((x+sign*y)%n+n)%n]
	}
	return string(out)
}

func csrfRandom() string {
	out := make([]byte, 32)
	for i := range out {
		index, err := rand.Int(rand.Reader, big.NewInt(int64(len(csrfAllowedChars))))
		if err != nil {
			panic("api schema: no randomness: " + err.Error())
		}
		out[i] = csrfAllowedChars[index.Int64()]
	}
	return string(out)
}

// djangoRequestCookie is request.COOKIES[name] (django.http.cookie.parse_cookie:
// the last occurrence wins; values are unquoted as http.cookies does).
func djangoRequestCookie(r *http.Request, name string) (string, bool) {
	value, found := "", false
	cookie := readRouteDjangoHeaderString(strings.Join(r.Header.Values("Cookie"), ","))
	for _, part := range strings.Split(cookie, ";") {
		key, candidate, hasValue := strings.Cut(part, "=")
		if !hasValue {
			key, candidate = "", key
		}
		if readRouteTrimPythonSpace(key) != name {
			continue
		}
		value, found = readRouteTrimPythonSpace(candidate), true
		if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
			quoted := value[1 : len(value)-1]
			decoded := make([]byte, 0, len(quoted))
			for i := 0; i < len(quoted); i++ {
				if quoted[i] == '\\' && i+1 < len(quoted) {
					if i+3 < len(quoted) && quoted[i+1] >= '0' && quoted[i+1] <= '3' &&
						quoted[i+2] >= '0' && quoted[i+2] <= '7' && quoted[i+3] >= '0' && quoted[i+3] <= '7' {
						decoded = append(decoded, (quoted[i+1]-'0')*64+(quoted[i+2]-'0')*8+quoted[i+3]-'0')
						i += 3
						continue
					}
					i++
				}
				decoded = append(decoded, quoted[i])
			}
			value = string(decoded)
		}
	}
	return value, found
}
