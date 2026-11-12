package apischema

import (
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyheader"
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
	Status int // 0 when a renderer was selected, else 404 (format), 406, or 500 (a ValueError from the Accept parameters)
	// NotPorted is set when an Accept parameter names a charset whose decoder is not ported.
	NotPorted bool
	Renderer  int // index into the renderer list
	MediaType string
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
		// order_by_precedence parses every value before anything is matched.
		_, outcome := parseMediaType(strings.TrimSpace(token))
		if outcome == pyheader.Invalid {
			return Negotiation{Status: 500}
		}
		if outcome == pyheader.NotPorted {
			return Negotiation{NotPorted: true}
		}
	}
	for _, token := range strings.Split(accept, ",") {
		token = strings.TrimSpace(token)
		if seen[token] {
			continue
		}
		seen[token] = true
		precedence := mustMediaType(token).precedence()
		groups[3-precedence] = append(groups[3-precedence], token)
	}
	for _, group := range groups {
		for _, index := range candidates {
			own := mustMediaType(renderers[index].MediaType)
			for _, token := range group {
				client := mustMediaType(token)
				if !own.matches(client) {
					continue
				}
				if own.precedence() > client.precedence() {
					full := renderers[index].MediaType
					for _, parameter := range client.params {
						full += ";" + parameter.Name + "=" + parameter.Value
					}
					return Negotiation{Renderer: index, MediaType: full}
				}
				return Negotiation{Renderer: index, MediaType: token}
			}
		}
	}
	return Negotiation{Status: 406}
}

type mediaParameter = pyheader.Param

type mediaType struct {
	main, sub string
	params    []mediaParameter
}

// parseMediaType is DRF's _MediaType over Django's parse_header_parameters.
func parseMediaType(value string) (mediaType, pyheader.Outcome) {
	full, params, outcome := pyheader.Parse(value)
	main, sub, _ := strings.Cut(full, "/")
	return mediaType{main: main, sub: sub, params: params}, outcome
}

func mustMediaType(value string) mediaType {
	media, outcome := parseMediaType(value)
	if outcome != pyheader.Parsed {
		panic("apischema: media type parsed before: " + value)
	}
	return media
}

func (m mediaType) param(name string) (string, bool) {
	for _, parameter := range m.params {
		if parameter.Name == name {
			return parameter.Value, true
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
	case len(m.params) == 0 || (len(m.params) == 1 && m.params[0].Name == "q"):
		return 2
	}
	return 3
}

func (m mediaType) matches(other mediaType) bool {
	for _, parameter := range m.params {
		if parameter.Name == "q" {
			continue
		}
		if value, ok := other.param(parameter.Name); !ok || value != parameter.Value {
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
// The outcome is Invalid when the media type no longer parses (a ValueError).
func JSONIndent(accepted string) (int, pyheader.Outcome) {
	_, params, outcome := pyheader.Parse(accepted)
	if outcome != pyheader.Parsed {
		return 4, outcome
	}
	for _, parameter := range params {
		if parameter.Name != "indent" {
			continue
		}
		value, ok := pythonInt(parameter.Value)
		if !ok {
			return 4, pyheader.Parsed
		}
		value = max(min(value, 8), 0)
		if value == 0 {
			return 4, pyheader.Parsed
		}
		return value, pyheader.Parsed
	}
	return 4, pyheader.Parsed
}

// pythonInt is int(str): optional surrounding whitespace and sign, decimal
// digits (any Unicode Nd digit) with single underscores between digits.
func pythonInt(value string) (int, bool) {
	value = strings.TrimFunc(value, pyheader.IsSpace)
	negative := false
	if value != "" && (value[0] == '+' || value[0] == '-') {
		negative = value[0] == '-'
		value = value[1:]
	}
	if value == "" || value[0] == '_' || value[len(value)-1] == '_' || strings.Contains(value, "__") {
		return 0, false
	}
	result := 0
	for _, r := range value {
		if r == '_' {
			continue
		}
		digit, ok := decimalDigit(r)
		if !ok {
			return 0, false
		}
		if result < 1<<30 {
			result = result*10 + digit
		}
	}
	if negative {
		result = -result
	}
	return result, true
}

// decimalDigit is unicodedata.decimal for a code point: Nd series start at a zero.
func decimalDigit(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	if r < 0x80 || !unicode.Is(unicode.Nd, r) {
		return 0, false
	}
	for _, rng := range unicode.Nd.R16 {
		if r >= rune(rng.Lo) && r <= rune(rng.Hi) {
			return int(r-rune(rng.Lo)) % 10, true
		}
	}
	for _, rng := range unicode.Nd.R32 {
		if r >= rune(rng.Lo) && r <= rune(rng.Hi) {
			return int(r-rune(rng.Lo)) % 10, true
		}
	}
	return 0, false
}
