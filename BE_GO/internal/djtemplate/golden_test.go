package djtemplate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// golden is testdata/django_renders.json, captured from Django's
// render_to_string by TESTS/adapters/go/document_template_golden.py.
type golden struct {
	Django string `json:"django"`
	Names  map[string]struct {
		Weekdays []string `json:"weekdays"`
		Months   []string `json:"months"`
	} `json:"names"`
	Renders []struct {
		Name     string                     `json:"name"`
		Template string                     `json:"template"`
		Language string                     `json:"language"`
		Context  map[string]json.RawMessage `json:"context"`
		SHA256   string                     `json:"sha256"`
		Length   int                        `json:"length"`
		Error    string                     `json:"error"`
	} `json:"renders"`
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

// TaggedValue decodes the golden data's {"t": kind, "v": value} values.
func TaggedValue(raw json.RawMessage) (any, error) {
	var tagged struct {
		T string          `json:"t"`
		V json.RawMessage `json:"v"`
	}
	if err := json.Unmarshal(raw, &tagged); err != nil {
		return nil, err
	}
	text := func() string {
		var value string
		json.Unmarshal(tagged.V, &value)
		return value
	}
	pairs := func() ([][2]json.RawMessage, error) {
		var items [][2]json.RawMessage
		err := json.Unmarshal(tagged.V, &items)
		return items, err
	}
	switch tagged.T {
	case "none":
		return nil, nil
	case "str":
		return text(), nil
	case "safe":
		return Safe(text()), nil
	case "bool":
		return string(tagged.V) == "true", nil
	case "int":
		var number int64
		err := json.Unmarshal(tagged.V, &number)
		return number, err
	case "float":
		var number float64
		err := json.Unmarshal(tagged.V, &number)
		return number, err
	case "decimal":
		return Decimal(text()), nil
	case "date":
		parsed, err := time.Parse("2006-01-02", text())
		return DateOf(parsed), err
	case "datetime":
		parsed, err := time.Parse("2006-01-02T15:04:05.999999-07:00", text())
		return DateTime{Time: parsed}, err
	case "list":
		var items []json.RawMessage
		if err := json.Unmarshal(tagged.V, &items); err != nil {
			return nil, err
		}
		list := List{}
		for _, item := range items {
			value, err := TaggedValue(item)
			if err != nil {
				return nil, err
			}
			list = append(list, value)
		}
		return list, nil
	case "dict", "obj":
		items, err := pairs()
		if err != nil {
			return nil, err
		}
		dict, attrs := NewDict(), map[string]any{}
		for _, pair := range items {
			var key string
			if err := json.Unmarshal(pair[0], &key); err != nil {
				return nil, err
			}
			value, err := TaggedValue(pair[1])
			if err != nil {
				return nil, err
			}
			dict.Set(key, value)
			attrs[key] = value
		}
		if tagged.T == "dict" {
			return dict, nil
		}
		object := NewObject("golden", attrs)
		object.open = true
		return object, nil
	case "call":
		value, err := TaggedValue(tagged.V)
		return Lazy(func() (any, error) { return value, nil }), err
	}
	return nil, errors.New("tag " + tagged.T)
}

func TestTemplatesMatchDjangoSources(t *testing.T) {
	for _, name := range Names {
		embedded, err := Source(name)
		if err != nil {
			t.Fatal(err)
		}
		source, err := os.ReadFile(filepath.Join("..", "..", "..", "BE", "templates", name))
		if err != nil {
			t.Fatal(err)
		}
		if string(embedded) != string(source) {
			t.Errorf("%s differs from BE/templates", name)
		}
	}
}

func TestDateNamesMatchDjango(t *testing.T) {
	data := loadGolden(t)
	for language, names := range data.Names {
		weekdays, months := weekdayNames[language], monthNames[language]
		if strings.Join(weekdays[:], ",") != strings.Join(names.Weekdays, ",") ||
			strings.Join(months[:], ",") != strings.Join(names.Months, ",") {
			t.Errorf("%s names differ", language)
		}
	}
}

func TestRendersMatchDjango(t *testing.T) {
	data := loadGolden(t)
	if data.Django != "5.2.18" || len(data.Renders) < 300 {
		t.Fatalf("golden data: django %s, %d renders", data.Django, len(data.Renders))
	}
	dump := os.Getenv("GOLDEN_DUMP")
	for _, render := range data.Renders {
		context := map[string]any{}
		for key, raw := range render.Context {
			value, err := TaggedValue(raw)
			if err != nil {
				t.Fatalf("%s: %v", render.Name, err)
			}
			context[key] = value
		}
		html, err := Render(render.Template, context, render.Language)
		if render.Error != "" {
			if err == nil {
				t.Errorf("%s: Django raised %s, Go rendered", render.Name, render.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", render.Name, err)
			continue
		}
		sum := sha256.Sum256([]byte(html))
		if hex.EncodeToString(sum[:]) != render.SHA256 || len(html) != render.Length {
			t.Errorf("%s: render differs (%d bytes, Django %d)", render.Name, len(html), render.Length)
			if dump != "" {
				os.WriteFile(filepath.Join(dump, render.Name+".go.html"), []byte(html), 0o644)
			}
		}
	}
}
