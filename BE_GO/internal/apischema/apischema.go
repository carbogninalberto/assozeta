// Package apischema serves the drf-spectacular OpenAPI documents that Django
// builds for schema/, plus the redoc and swagger-ui pages that load them.
//
// Django generates the document at request time from its URL configuration and
// the active translation catalogs; it only changes with the Django code or its
// installed packages. TESTS/adapters/django/api_schema.py renders it through
// Django for every reachable catalog selection and stores the result here:
// schema.yaml and schema.json are the documents for the default language, and
// catalog.json holds the line patches of every other language, the OPTIONS
// bodies and the HTML pages. CI regenerates the data and fails when it is stale.
package apischema

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
)

//go:embed schema.yaml
var schemaYAML string

//go:embed schema.json
var schemaJSON string

//go:embed catalog.json
var catalogJSON []byte

// Format names a drf-spectacular renderer family.
type Format string

const (
	FormatYAML Format = "yaml"
	FormatJSON Format = "json"
)

type patch struct {
	start, end  int
	replacement []string
}

func (p *patch) UnmarshalJSON(data []byte) error {
	var raw []json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil || len(raw) != 3 {
		return fmt.Errorf("apischema: malformed patch")
	}
	if err := json.Unmarshal(raw[0], &p.start); err != nil {
		return err
	}
	if err := json.Unmarshal(raw[1], &p.end); err != nil {
		return err
	}
	return json.Unmarshal(raw[2], &p.replacement)
}

type catalogData struct {
	LanguageCode string                       `json:"language_code"`
	Title        string                       `json:"title"`
	Version      string                       `json:"version"`
	VersionLines map[Format]int               `json:"version_lines"`
	Directories  [][]string                   `json:"directories"`
	LocaleAlias  map[string]string            `json:"locale_alias"`
	VariantOf    map[string]int               `json:"variant_of"`
	Variants     []map[Format][]patch         `json:"variants"`
	Options      map[string]map[Format]string `json:"options"`
	Pages        map[string]string            `json:"pages"`
}

type data struct {
	catalogData
	base        map[Format][]string
	directories []map[string]struct{}
}

var loaded = sync.OnceValue(func() *data {
	result := &data{base: map[Format][]string{FormatYAML: splitLines(schemaYAML), FormatJSON: splitLines(schemaJSON)}}
	if err := json.Unmarshal(catalogJSON, &result.catalogData); err != nil {
		panic("apischema: embedded catalog: " + err.Error())
	}
	for _, names := range result.Directories {
		set := make(map[string]struct{}, len(names))
		for _, name := range names {
			set[name] = struct{}{}
		}
		result.directories = append(result.directories, set)
	}
	return result
})

// splitLines mirrors str.splitlines(keepends=True) for the generated documents,
// which only contain "\n" line ends.
func splitLines(text string) []string {
	lines := strings.SplitAfter(text, "\n")
	if lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

// Title is SPECTACULAR_SETTINGS['TITLE'].
func Title() string { return loaded().Title }

// LanguageCode is settings.LANGUAGE_CODE.
func LanguageCode() string { return loaded().LanguageCode }

// Document returns the schema rendered by Django while the language with the
// given catalog key is active. version is the non-empty `version` query
// value, or "". ok is false for a catalog key the generator never rendered.
func Document(format Format, key, version string) (string, bool) {
	d := loaded()
	variant, ok := d.VariantOf[key]
	if !ok {
		return "", false
	}
	base := d.base[format]
	var out strings.Builder
	out.Grow(len(schemaJSON) + 4096)
	versionLine := d.VersionLines[format]
	write := func(from, to int) {
		for i := from; i < to; i++ {
			if i == versionLine && version != "" {
				// build_root_object: f'{settings.VERSION} ({version})'. The caller
				// restricts version to characters both renderers emit verbatim.
				out.WriteString(strings.Replace(base[i], d.Version, d.Version+" ("+version+")", 1))
				continue
			}
			out.WriteString(base[i])
		}
	}
	position := 0
	for _, op := range d.Variants[variant][format] {
		write(position, op.start)
		for _, line := range op.replacement {
			out.WriteString(line)
		}
		position = op.end
	}
	write(position, len(base))
	return out.String(), true
}

// Options returns the OPTIONS body for LocaleMiddleware's language.
func Options(format Format, language string) (string, bool) {
	bodies, ok := loaded().Options[language]
	if !ok {
		return "", false
	}
	return bodies[format], true
}

// Page returns a generated HTML page: "swagger" (with the {{csrf_token}} and
// {{schema_url}} placeholders), "redoc" (with {{schema_url}}) or
// "redoc_options".
func Page(name string) string { return loaded().Pages[name] }
