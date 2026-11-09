package apischema

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// The testdata files are generated from Django and CPython by
// TESTS/adapters/django/api_schema.py together with the embedded documents.

func readTestdata(t *testing.T, name string, target any) {
	t.Helper()
	raw, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, target); err != nil {
		t.Fatal(err)
	}
}

func TestCatalogKeyMatchesGettext(t *testing.T) {
	var samples map[string]string
	readTestdata(t, "catalog_keys.json", &samples)
	if len(samples) < 200 {
		t.Fatalf("only %d samples", len(samples))
	}
	for language, want := range samples {
		got, ok := CatalogKey(language)
		if !ok || got != want {
			t.Errorf("CatalogKey(%q) = %q, %v; Django selects %q", language, got, ok, want)
		}
	}
	for _, refused := range []string{"../it", "it/x", "a\x00", "/"} {
		if _, ok := CatalogKey(refused); ok {
			t.Errorf("CatalogKey(%q) is accepted", refused)
		}
	}
}

func TestEveryLanguageHasADocument(t *testing.T) {
	d := loaded()
	for language := range d.Options {
		key, ok := CatalogKey(language)
		if !ok {
			t.Fatalf("no key for %q", language)
		}
		for _, format := range []Format{FormatYAML, FormatJSON} {
			if _, ok := Document(format, key, ""); !ok {
				t.Errorf("no %s document for %q (%s)", format, language, key)
			}
		}
	}
}

func TestDefaultDocumentIsTheEmbeddedOne(t *testing.T) {
	key, _ := CatalogKey(LanguageCode())
	for format, want := range map[Format]string{FormatYAML: schemaYAML, FormatJSON: schemaJSON} {
		got, ok := Document(format, key, "")
		if !ok || got != want {
			t.Errorf("%s default document differs from the embedded file", format)
		}
		versioned, _ := Document(format, key, "v2")
		if strings.Count(versioned, "1.0.0 (v2)") != 1 || len(versioned) != len(want)+len(" (v2)") {
			t.Errorf("%s version substitution is not a single in-place change", format)
		}
	}
	var parsed map[string]any
	english, _ := CatalogKey("en")
	document, _ := Document(FormatJSON, english, "")
	if err := json.Unmarshal([]byte(document), &parsed); err != nil || parsed["openapi"] != "3.0.3" {
		t.Errorf("patched English JSON does not parse: %v", err)
	}
}

func TestRenderersMatchDRF(t *testing.T) {
	var corpus []struct {
		Text  string `json:"text"`
		Shape int    `json:"shape"`
		YAML  string `json:"yaml"`
		JSON  string `json:"json"`
	}
	readTestdata(t, "render_corpus.json", &corpus)
	for _, item := range corpus {
		detail := func(code string) ErrorDetail { return ErrorDetail{String: item.Text, Code: code} }
		token := func(value string) ErrorDetail { return ErrorDetail{String: value, Code: "token_not_valid"} }
		var data Map
		switch item.Shape {
		case 0:
			data = Map{{"detail", detail("some_code")}}
		case 1:
			data = Map{{"detail", detail("user_not_found")}, {"code", ErrorDetail{"user_not_found", "user_not_found"}}}
		case 2:
			data = Map{{"detail", token(item.Text)}, {"code", token("token_not_valid")}, {"messages", []Value{Map{
				{"token_class", token("AccessToken")}, {"token_type", token("access")}, {"message", token(item.Text)}}}}}
		case 3:
			data = Map{{"detail", detail("permission_denied")}, {"impersonation_invalid", ErrorDetail{"True", "permission_denied"}}}
		}
		if got := RenderYAML(data); got != item.YAML {
			t.Errorf("YAML shape %d for %q:\n got %q\nwant %q", item.Shape, item.Text, got, item.YAML)
		}
		if got := RenderJSON(data, 4); got != item.JSON {
			t.Errorf("JSON shape %d for %q:\n got %q\nwant %q", item.Shape, item.Text, got, item.JSON)
		}
	}
	if len(corpus) < 300 {
		t.Fatalf("corpus has %d cases", len(corpus))
	}
}

func TestNegotiationMatchesDRF(t *testing.T) {
	var corpus []struct {
		View      string  `json:"view"`
		Accept    *string `json:"accept"`
		Format    *string `json:"format"`
		Outcome   int     `json:"outcome"`
		Renderer  int     `json:"renderer"`
		MediaType string  `json:"media_type"`
		Indent    *int    `json:"indent"`
	}
	readTestdata(t, "negotiation_corpus.json", &corpus)
	for _, item := range corpus {
		renderers := SchemaRenderers
		if item.View == "page" {
			renderers = PageRenderers
		}
		accept, format := "", ""
		if item.Accept != nil {
			accept = *item.Accept
		}
		if item.Format != nil {
			format = *item.Format
		}
		if !SupportedAccept(accept) {
			t.Errorf("corpus Accept %q is refused", accept)
			continue
		}
		got := Negotiate(renderers, accept, item.Accept != nil, format)
		want := Negotiation{Renderer: item.Renderer, MediaType: item.MediaType}
		if item.Outcome != 200 {
			want = Negotiation{Status: item.Outcome}
		}
		if got != want {
			t.Errorf("%s Accept %q format %q: got %+v, want %+v", item.View, accept, format, got, want)
		}
		if item.Indent != nil || (item.Outcome == 200 && renderers[item.Renderer].Format == "json") {
			wantIndent := 4
			if item.Indent != nil && *item.Indent != 0 {
				wantIndent = *item.Indent
			}
			if indent := JSONIndent(got.MediaType); indent != wantIndent {
				t.Errorf("indent for %q = %d, want %d", got.MediaType, indent, wantIndent)
			}
		}
	}
	for _, refused := range []string{`application/json; indent="2"`, "text/html; title*=utf-8''x", "application/json\x01", "ÿ/*"} {
		if SupportedAccept(refused) {
			t.Errorf("SupportedAccept(%q) accepts", refused)
		}
	}
}
