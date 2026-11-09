package apischema

import (
	"strings"
)

// Renderer is a DRF renderer class as content negotiation sees it.
type Renderer struct {
	MediaType, Format, Charset string
}

var (
	// SchemaRenderers are SpectacularAPIView.renderer_classes, in order.
	SchemaRenderers = []Renderer{
		{"application/vnd.oai.openapi", "yaml", "utf-8"},
		{"application/yaml", "yaml", "utf-8"},
		{"application/vnd.oai.openapi+json", "json", ""},
		{"application/json", "json", ""},
	}
	// PageRenderers are the redoc and swagger views' [TemplateHTMLRenderer].
	PageRenderers = []Renderer{{"text/html", "html", "utf-8"}}
)

// Negotiation is the outcome of DRF's DefaultContentNegotiation.
type Negotiation struct {
	Status    int // 0 when a renderer was selected, else 404 (format) or 406
	Renderer  int // index into the renderer list
	MediaType string
}

// SupportedAccept reports whether every Accept value stays within the media
// range syntax this port parses exactly as Django's parse_header_parameters
// (email.message parameters): quoted strings, comments, RFC 2231 names and
// non-ASCII bytes are not supported.
func SupportedAccept(accept string) bool {
	for i := 0; i < len(accept); i++ {
		c := accept[i]
		if c != '\t' && (c < 0x20 || c > 0x7E) {
			return false
		}
		if strings.IndexByte("\"\\<>()", c) >= 0 {
			return false
		}
	}
	for _, token := range strings.Split(accept, ",") {
		parameters := strings.Split(token, ";")
		for _, parameter := range parameters[1:] {
			if name, _, _ := strings.Cut(parameter, "="); strings.Contains(name, "*") {
				return false
			}
		}
	}
	return true
}

// Negotiate mirrors DefaultContentNegotiation.select_renderer. accept is the
// Accept header (present reports whether it was sent; DRF defaults to */*),
// format the `format` query value.
//
// Django groups Accept values of equal precedence in a set, so with several
// values of one precedence matching the same renderer it returns any of them.
// This port takes them in header order.
func Negotiate(renderers []Renderer, accept string, present bool, format string) Negotiation {
	candidates := make([]int, 0, len(renderers))
	for index, renderer := range renderers {
		if format == "" || renderer.Format == format {
			candidates = append(candidates, index)
		}
	}
	if len(candidates) == 0 {
		return Negotiation{Status: 404}
	}
	if !present {
		accept = "*/*"
	}
	var groups [4][]string
	seen := map[string]bool{}
	for _, token := range strings.Split(accept, ",") {
		token = strings.TrimSpace(token)
		if seen[token] {
			continue
		}
		seen[token] = true
		precedence := parseMediaType(token).precedence()
		groups[3-precedence] = append(groups[3-precedence], token)
	}
	for _, group := range groups {
		for _, index := range candidates {
			own := parseMediaType(renderers[index].MediaType)
			for _, token := range group {
				client := parseMediaType(token)
				if !own.matches(client) {
					continue
				}
				if own.precedence() > client.precedence() {
					full := renderers[index].MediaType
					for _, parameter := range client.params {
						full += ";" + parameter.name + "=" + parameter.value
					}
					return Negotiation{Renderer: index, MediaType: full}
				}
				return Negotiation{Renderer: index, MediaType: token}
			}
		}
	}
	return Negotiation{Status: 406}
}

type mediaParameter struct{ name, value string }

type mediaType struct {
	main, sub string
	params    []mediaParameter
}

// parseMediaType is DRF's _MediaType over Django's parse_header_parameters.
func parseMediaType(value string) mediaType {
	full, params := parseHeaderParameters(value)
	main, sub, _ := strings.Cut(full, "/")
	return mediaType{main: main, sub: sub, params: params}
}

func parseHeaderParameters(line string) (string, []mediaParameter) {
	if line == "" {
		return "", nil
	}
	if !strings.Contains(line, ";") {
		return strings.ToLower(strings.TrimSpace(line)), nil
	}
	var key string
	var params []mediaParameter
	for index, part := range strings.Split(line, ";") {
		// email.message._parseparam lower-cases only names given with a value.
		name, value := strings.TrimSpace(part), ""
		if position := strings.IndexByte(part, '='); position >= 0 {
			name = strings.ToLower(strings.TrimSpace(part[:position]))
			value = strings.TrimSpace(part[position+1:])
		}
		if index == 0 {
			key = strings.ToLower(name)
			continue
		}
		if name == "" {
			continue
		}
		replaced := false
		for i := range params {
			if params[i].name == name {
				params[i].value, replaced = value, true
			}
		}
		if !replaced {
			params = append(params, mediaParameter{name, value})
		}
	}
	return key, params
}

func (m mediaType) param(name string) (string, bool) {
	for _, parameter := range m.params {
		if parameter.name == name {
			return parameter.value, true
		}
	}
	return "", false
}

func (m mediaType) precedence() int {
	switch {
	case m.main == "*":
		return 0
	case m.sub == "*":
		return 1
	case len(m.params) == 0 || (len(m.params) == 1 && m.params[0].name == "q"):
		return 2
	}
	return 3
}

func (m mediaType) matches(other mediaType) bool {
	for _, parameter := range m.params {
		if parameter.name == "q" {
			continue
		}
		if value, ok := other.param(parameter.name); !ok || value != parameter.value {
			return false
		}
	}
	if m.sub != "*" && other.sub != "*" && other.sub != m.sub {
		return false
	}
	return m.main == "*" || other.main == "*" || other.main == m.main
}

// JSONIndent is OpenApiJsonRenderer.get_indent for the accepted media type:
// an `indent` parameter clamped to 0..8, where 0 (and its absence) means 4.
func JSONIndent(accepted string) int {
	_, params := parseHeaderParameters(accepted)
	for _, parameter := range params {
		if parameter.name != "indent" {
			continue
		}
		value, ok := pythonInt(parameter.value)
		if !ok {
			return 4
		}
		value = max(min(value, 8), 0)
		if value == 0 {
			return 4
		}
		return value
	}
	return 4
}

// pythonInt is int(str) for ASCII input: optional surrounding whitespace and
// sign, decimal digits with single underscores between digits.
func pythonInt(value string) (int, bool) {
	value = strings.Trim(value, " \t\n\r\v\f\x1c\x1d\x1e\x1f")
	negative := false
	if value != "" && (value[0] == '+' || value[0] == '-') {
		negative = value[0] == '-'
		value = value[1:]
	}
	if value == "" || value[0] == '_' || value[len(value)-1] == '_' || strings.Contains(value, "__") {
		return 0, false
	}
	result := 0
	for i := 0; i < len(value); i++ {
		c := value[i]
		if c == '_' {
			continue
		}
		if c < '0' || c > '9' {
			return 0, false
		}
		if result < 1<<30 {
			result = result*10 + int(c-'0')
		}
	}
	if negative {
		result = -result
	}
	return result, true
}
