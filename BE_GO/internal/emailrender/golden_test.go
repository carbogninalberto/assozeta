package emailrender

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

// golden is testdata/django_renders.json, captured from Django's
// render_to_string by TESTS/adapters/go/email_render_golden.py.
type golden struct {
	Django   string `json:"django"`
	Settings struct {
		AppURL         string `json:"APP_URL"`
		WhitelabelName string `json:"WHITELABEL_NAME"`
		IsWhitelabel   bool   `json:"IS_WHITELABEL"`
	} `json:"settings"`
	Configs map[string]*struct {
		SelfHosted   bool   `json:"self_hosted"`
		PrimaryColor string `json:"primary_color"`
		Name         string `json:"name"`
		LogoPath     string `json:"logo_path"`
		UpdatedAt    string `json:"updated_at"`
	} `json:"configs"`
	Renders []struct {
		Name     string                     `json:"name"`
		Template string                     `json:"template"`
		Context  map[string]json.RawMessage `json:"context"`
		Language string                     `json:"language"`
		Config   string                     `json:"config"`
		HTML     string                     `json:"html"`
	} `json:"renders"`
	WhitelabelLogo []struct {
		Config string         `json:"config"`
		Logo   map[string]any `json:"logo"`
	} `json:"whitelabel_logo"`
	DecimalSeparators map[string]string `json:"decimal_separators"`
	Palettes          []struct {
		Color      string `json:"color"`
		Primary    string `json:"primary"`
		Hover      string `json:"hover"`
		Foreground string `json:"foreground"`
	} `json:"palettes"`
	ForegroundDigest string           `json:"foreground_black_sha256"`
	LinearBits       []uint64         `json:"linear_bits"`
	Hover            map[string][]int `json:"hover"`
}

func loadGolden(t *testing.T) golden {
	t.Helper()
	raw, err := os.ReadFile("testdata/django_renders.json")
	if err != nil {
		t.Fatal(err)
	}
	var data golden
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatal(err)
	}
	return data
}

func (g golden) config(t *testing.T, name string) *InstanceConfig {
	spec := g.Configs[name]
	if spec == nil {
		return nil
	}
	updated, err := time.Parse(time.RFC3339Nano, spec.UpdatedAt)
	if err != nil {
		t.Fatal(err)
	}
	return &InstanceConfig{SelfHosted: spec.SelfHosted, PrimaryColor: spec.PrimaryColor, Name: spec.Name, LogoPath: spec.LogoPath, UpdatedAt: updated}
}

func (g golden) settings() Settings {
	return Settings{AppURL: g.Settings.AppURL, WhitelabelName: g.Settings.WhitelabelName, IsWhitelabel: g.Settings.IsWhitelabel}
}

func taggedValue(t *testing.T, raw json.RawMessage) any {
	t.Helper()
	var tagged struct {
		T string          `json:"t"`
		V json.RawMessage `json:"v"`
	}
	if err := json.Unmarshal(raw, &tagged); err != nil {
		t.Fatal(err)
	}
	switch tagged.T {
	case "none":
		return nil
	case "str":
		var text string
		json.Unmarshal(tagged.V, &text)
		return text
	case "bool":
		return string(tagged.V) == "true"
	case "int":
		number, err := strconv.ParseInt(string(tagged.V), 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		return number
	case "decimal":
		var text string
		json.Unmarshal(tagged.V, &text)
		return Decimal(text)
	case "float":
		number, err := strconv.ParseFloat(string(tagged.V), 64)
		if err != nil {
			t.Fatal(err)
		}
		return number
	case "dict":
		var items map[string]json.RawMessage
		json.Unmarshal(tagged.V, &items)
		result := map[string]any{}
		for key, item := range items {
			result[key] = taggedValue(t, item)
		}
		return result
	}
	t.Fatalf("tag %q", tagged.T)
	return nil
}

func TestTemplatesMatchDjangoSources(t *testing.T) {
	for _, name := range names {
		embedded, err := files.ReadFile("templates/" + name)
		if err != nil {
			t.Fatal(err)
		}
		source, err := os.ReadFile("../../../BE/templates/" + name)
		if err != nil {
			t.Fatal(err)
		}
		if string(embedded) != string(source) {
			t.Fatalf("%s differs from BE/templates", name)
		}
	}
}

func TestRendersMatchDjango(t *testing.T) {
	data := loadGolden(t)
	if data.Django != "5.2.18" {
		t.Fatalf("golden captured with Django %s", data.Django)
	}
	if len(data.Renders) < 20 {
		t.Fatalf("only %d renders", len(data.Renders))
	}
	for _, render := range data.Renders {
		t.Run(render.Name, func(t *testing.T) {
			brand, err := Brand(data.config(t, render.Config), data.settings())
			if err != nil {
				t.Fatal(err)
			}
			context := map[string]any{}
			for key, raw := range render.Context {
				context[key] = taggedValue(t, raw)
			}
			html, err := Render(render.Template, context, Options{Language: render.Language, Brand: brand})
			if err != nil {
				t.Fatal(err)
			}
			if html != render.HTML {
				t.Fatalf("render differs\n--- go\n%s\n--- django\n%s", html, render.HTML)
			}
		})
	}
}

func TestWhitelabelLogo(t *testing.T) {
	data := loadGolden(t)
	settings := data.settings()
	settings.IsWhitelabel = true
	for _, item := range data.WhitelabelLogo {
		logo, err := brandLogo(data.config(t, item.Config), settings)
		if err != nil {
			t.Fatal(err)
		}
		if item.Logo == nil {
			if logo != nil {
				t.Fatalf("%s: logo %v", item.Config, logo)
			}
			continue
		}
		if !reflect.DeepEqual(logo, item.Logo) {
			t.Fatalf("%s: %v != %v", item.Config, logo, item.Logo)
		}
	}
}

func TestDecimalSeparatorsMatchDjango(t *testing.T) {
	data := loadGolden(t)
	if !reflect.DeepEqual(decimalSeparators, data.DecimalSeparators) {
		t.Fatalf("separators differ: %v", data.DecimalSeparators)
	}
}

func TestPaletteMatchesDjango(t *testing.T) {
	data := loadGolden(t)
	for i, bits := range data.LinearBits {
		if linearChannels[i] != bits {
			t.Fatalf("linear channel %d", i)
		}
	}
	for target, values := range data.Hover {
		number, _ := strconv.Atoi(target)
		for channel, expected := range values {
			if got := hoverChannel(channel, number); got != expected {
				t.Fatalf("hover %d/%d: %d != %d", channel, number, got, expected)
			}
		}
	}
	for _, palette := range data.Palettes {
		got := Palette(&InstanceConfig{SelfHosted: true, PrimaryColor: palette.Color})
		if got != (PaletteColors{palette.Primary, palette.Hover, palette.Foreground}) {
			t.Fatalf("%s: %+v", palette.Color, got)
		}
	}
	digest := sha256.New()
	buffer := make([]byte, 1<<16)
	for red := 0; red < 256; red++ {
		for green := 0; green < 256; green++ {
			for blue := 0; blue < 256; blue++ {
				light := luminance([3]int{red, green, blue})
				buffer[green<<8|blue] = 1
				if 1.05/(light+.05) >= (light+.05)/.05 {
					buffer[green<<8|blue] = 0
				}
			}
		}
		digest.Write(buffer)
	}
	if hex.EncodeToString(digest.Sum(nil)) != data.ForegroundDigest {
		t.Fatal("text colour differs from Django for some 24-bit colour")
	}
}

func TestRefusesUnportedInput(t *testing.T) {
	brand, err := Brand(nil, Settings{AppURL: "https://x", WhitelabelName: "W"})
	if err != nil {
		t.Fatal(err)
	}
	options := Options{Language: "it", Brand: brand}
	for name, context := range map[string]map[string]any{
		"list value":       {"recipient_name": []any{"a"}},
		"struct value":     {"amount": struct{}{}},
		"non-finite float": {"amount": math.Inf(1)},
		"malformed dec":    {"amount": Decimal("1,5")},
		"text amount":      {"amount": "12"},
		"bool amount":      {"amount": true},
	} {
		if _, err := Render(PaymentReceipt, context, options); !errors.Is(err, ErrUnsupported) {
			t.Fatalf("%s: %v", name, err)
		}
	}
	if _, err := Render(CollaboratorInvite, map[string]any{"sport_association": "x"}, options); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("attribute of a string: %v", err)
	}
	if _, err := Render(PaymentReceipt, map[string]any{}, Options{Language: "xx", Brand: brand}); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("unknown language: %v", err)
	}
	if _, err := Render(PaymentReceipt, map[string]any{}, Options{Language: "it"}); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("missing brand: %v", err)
	}
	if _, err := Render("email/other.html", map[string]any{}, options); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("unknown template: %v", err)
	}
	for _, source := range []string{
		`{% for x in y %}{% endfor %}`, `{{ a|upper }}`, `{{ a|floatformat:"2g" }}x`, `{% if a == b %}{% endif %}`,
		`{# c #}`, `{% load humanize %}`, `{% include "x" %}`, `{{ "lit" }}`, `{% block a %}`, `{% trans "x" %}`,
		`{{ block.super }}`, `x{% extends "a" %}{% if a %}{% endif %}`,
	} {
		parsed, err := parseTemplate(source)
		if err == nil {
			r := &renderer{separator: ",", context: map[string]any{"a": int64(1)}}
			err = r.nodes(new(strings.Builder), parsed.nodes)
		}
		if !errors.Is(err, ErrUnsupported) {
			t.Fatalf("%s: %v", source, err)
		}
	}
}

func TestFloatRepr(t *testing.T) {
	for value, expected := range map[float64]string{
		0: "0.0", 1e16: "1e+16", 1.5e-05: "1.5e-05", 0.0001: "0.0001", sum(10.1, 0.2): "10.299999999999999", 123.0: "123.0",
		1e22: "1e+22", 5e-324: "5e-324", 1.7976931348623157e308: "1.7976931348623157e+308", 9999999999999998: "9999999999999998.0",
	} {
		got, err := pythonFloatRepr(value)
		if err != nil || got != expected {
			t.Fatalf("%v: %q != %q (%v)", value, got, expected, err)
		}
	}
	if got, _ := pythonFloatRepr(math.Copysign(0, -1)); got != "-0.0" {
		t.Fatal(got)
	}
}

// sum adds at run time (Go folds constant expressions exactly).
func sum(a, b float64) float64 { return a + b }
