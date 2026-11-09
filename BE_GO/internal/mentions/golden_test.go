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

func TestRefusesUnsupportedMarkup(t *testing.T) {
	for _, html := range []string{
		"<!-- c -->", "<!DOCTYPE html>", "<?xml?>", "<script>x</script>", "<style>p{}</style>", "<pre> x </pre>",
		"<textarea>x</textarea>", "a < b", "<p>x</b>", "<p>x</p></p>", "</br>", "&copy;", "&foo;", "&#150;", "&#0;", "x &",
		"&amp", `<a href="?a=1&b=2">x</a>`, `<p class=x>`, `<p class="a" class="b">`, "<p", `<a title="x>y">`, "\ufeffx",
		"<p>@nome \u212a</p>", "<p>a<br>b<br/>c</p>",
	} {
		if _, err := Filter(html, true, Context{"associate": func(string) (string, error) { return "v", nil }}); !errors.Is(err, ErrUnsupported) {
			t.Errorf("%q: %v", html, err)
		}
	}
}
