package mentions

import (
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

// golden is testdata/django_mentions.json, captured from filter_mentions by
// TESTS/adapters/go/document_template_golden.py.
type golden struct {
	BS4   string `json:"bs4"`
	Cases []struct {
		Name    string                     `json:"name"`
		HTML    *string                    `json:"html"`
		Context map[string]json.RawMessage `json:"context"`
		Output  string                     `json:"output"`
	} `json:"cases"`
}

type tagged struct {
	T string          `json:"t"`
	V json.RawMessage `json:"v"`
}

// goldenStr is str(value) of a tagged scalar; nil for None and containers
// are returned as their tagged pairs.
func goldenStr(raw json.RawMessage) (string, bool, error) {
	var value tagged
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", false, err
	}
	var text string
	switch value.T {
	case "none":
		return "", false, nil
	case "str", "decimal":
		json.Unmarshal(value.V, &text)
		return text, true, nil
	case "int":
		return string(value.V), true, nil
	case "bool":
		if string(value.V) == "true" {
			return "True", true, nil
		}
		return "False", true, nil
	case "date":
		json.Unmarshal(value.V, &text)
		return text, true, nil
	case "datetime":
		json.Unmarshal(value.V, &text)
		parsed, err := time.Parse("2006-01-02T15:04:05.999999-07:00", text)
		if err != nil {
			return "", false, err
		}
		out := parsed.UTC().Format("2006-01-02 15:04:05")
		if micro := parsed.Nanosecond() / 1000; micro != 0 {
			out += "." + strings.Repeat("0", 6-len(strconv.Itoa(micro))) + strconv.Itoa(micro)
		}
		return out + "+00:00", true, nil
	}
	return "", false, errors.New("str() of " + value.T)
}

// goldenResolver is get_nested_attr over tagged objects (attributes) and dicts (.get).
func goldenResolver(raw json.RawMessage) Resolver {
	return func(path string) (string, error) {
		current := raw
		for _, attr := range strings.Split(path, ".") {
			var value tagged
			if err := json.Unmarshal(current, &value); err != nil {
				return "", err
			}
			if value.T == "none" {
				return "", nil
			}
			if value.T != "obj" && value.T != "dict" {
				return "", errors.New("attribute of " + value.T)
			}
			var pairs [][2]json.RawMessage
			if err := json.Unmarshal(value.V, &pairs); err != nil {
				return "", err
			}
			current = json.RawMessage(`{"t":"none"}`)
			for _, pair := range pairs {
				var key string
				json.Unmarshal(pair[0], &key)
				if key == attr {
					current = pair[1]
				}
			}
		}
		text, present, err := goldenStr(current)
		if err != nil || !present {
			return "", err
		}
		return text, nil
	}
}

func TestFilterMatchesDjango(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_mentions.json")
	if err != nil {
		t.Fatal(err)
	}
	var data golden
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatal(err)
	}
	if data.BS4 != "4.13.3" || len(data.Cases) < 400 {
		t.Fatalf("golden data: bs4 %s, %d cases", data.BS4, len(data.Cases))
	}
	for _, c := range data.Cases {
		context := Context{}
		for name, value := range c.Context {
			context[name] = goldenResolver(value)
		}
		html := ""
		if c.HTML != nil {
			html = *c.HTML
		}
		output, err := Filter(html, c.HTML != nil, context)
		if err != nil {
			t.Errorf("%s: %v", c.Name, err)
			continue
		}
		if output != c.Output {
			t.Errorf("%s:\n go     %q\n django %q", c.Name, output, c.Output)
		}
	}
}

// filterGolden is testdata/filter_python.json, captured by
// testdata/gen_filter_python.py from the real filter_mentions
// (BE/application/models/utils.py:151-216) with a plain dict context.
type filterGolden struct {
	BS4     string                    `json:"bs4"`
	Context map[string]map[string]any `json:"context"`
	Cases   []struct {
		Name   string  `json:"name"`
		HTML   string  `json:"html"`
		Output *string `json:"output"` // nil: filter_mentions raised
		Blank  *bool   `json:"blank"`
		Error  *string `json:"error"`
	} `json:"cases"`
}

// filterGoldenResolver is get_nested_attr over decoded JSON objects: a
// missing attribute and a JSON null are both str() of None, i.e. "".
func filterGoldenResolver(object map[string]any) Resolver {
	return func(path string) (string, error) {
		var current any = object
		for _, attr := range strings.Split(path, ".") {
			if current == nil {
				return "", nil
			}
			fields, ok := current.(map[string]any)
			if !ok {
				return "", errors.New("attribute of a non-object")
			}
			current = fields[attr]
		}
		if current == nil {
			return "", nil
		}
		text, ok := current.(string)
		if !ok {
			return "", errors.New("str() of a non-string")
		}
		return text, nil
	}
}

// TestFilterMatchesPythonGolden checks the markup the old parser refused
// (comments, doctypes, processing instructions, CDATA, script/style/textarea,
// mismatched end tags, odd attributes, entity edge cases, case folding) and
// the FilterBlank answer against the real filter_mentions.
func TestFilterMatchesPythonGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/filter_python.json")
	if err != nil {
		t.Fatal(err)
	}
	var data filterGolden
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatal(err)
	}
	if data.BS4 != "4.13.3" || len(data.Cases) < 50 {
		t.Fatalf("golden data: bs4 %s, %d cases", data.BS4, len(data.Cases))
	}
	context := Context{}
	for name, object := range data.Context {
		context[name] = filterGoldenResolver(object)
	}
	if len(context) == 0 {
		t.Fatal("golden data has no context")
	}
	for _, c := range data.Cases {
		output, err := Filter(c.HTML, true, context)
		if c.Error != nil {
			if !errors.Is(err, ErrAliasKeyError) {
				t.Errorf("%s: got %v, want %s", c.Name, err, *c.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", c.Name, err)
			continue
		}
		if c.Output == nil || c.Blank == nil {
			t.Errorf("%s: golden data has no output", c.Name)
			continue
		}
		if output != *c.Output {
			t.Errorf("%s: %q\n python %q", c.Name, output, *c.Output)
		}
		blankOutput, blank, err := FilterBlank(c.HTML, context)
		if err != nil {
			t.Errorf("%s: FilterBlank: %v", c.Name, err)
			continue
		}
		if blankOutput != output || blank != *c.Blank {
			t.Errorf("%s: FilterBlank %q %v, want %q %v", c.Name, blankOutput, blank, output, *c.Blank)
		}
	}
}
