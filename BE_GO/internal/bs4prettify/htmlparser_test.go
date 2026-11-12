package bs4prettify

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// pyText is a Python str from the corpus: a JSON string, or {"hex": "..."}
// for a value holding a lone surrogate (the hex is the UTF-8 byte form this
// code base uses for such strings).
type pyText struct {
	value string
}

func (t *pyText) UnmarshalJSON(data []byte) error {
	var text string
	if err := json.Unmarshal(data, &text); err == nil {
		t.value = text
		return nil
	}
	var encoded struct {
		Hex string `json:"hex"`
	}
	if err := json.Unmarshal(data, &encoded); err != nil {
		return err
	}
	raw, err := hex.DecodeString(encoded.Hex)
	if err != nil {
		return err
	}
	t.value = string(raw)
	return nil
}

// tokenizeCase is one testdata/full.json entry; only the tokenizer fields are
// decoded here.
type tokenizeCase struct {
	Kind        string            `json:"kind"`
	Input       pyText            `json:"input"`
	Events      []json.RawMessage `json:"events"`
	EventsError *string           `json:"events_error"`
}

// tokenizeEvent is one recorded handler callback, in comparable form.
type tokenizeEvent struct {
	kind  string
	text  string
	attrs []tokenizeAttr
}

type tokenizeAttr struct {
	name  string
	value *string // nil: html.parser reported None
}

func decodeTokenizeEvents(t *testing.T, raw []json.RawMessage) []tokenizeEvent {
	t.Helper()
	events := make([]tokenizeEvent, 0, len(raw))
	for _, item := range raw {
		var parts []json.RawMessage
		if err := json.Unmarshal(item, &parts); err != nil || len(parts) < 2 {
			t.Fatalf("bad event %s: %v", item, err)
		}
		event := tokenizeEvent{}
		decodeTokenizeText(t, parts[0], &event.kind)
		decodeTokenizeText(t, parts[1], &event.text)
		if len(parts) > 2 {
			var pairs []json.RawMessage
			if err := json.Unmarshal(parts[2], &pairs); err != nil {
				t.Fatalf("bad attrs %s: %v", parts[2], err)
			}
			for _, pair := range pairs {
				var fields []json.RawMessage
				if err := json.Unmarshal(pair, &fields); err != nil || len(fields) != 2 {
					t.Fatalf("bad attr %s: %v", pair, err)
				}
				attr := tokenizeAttr{}
				decodeTokenizeText(t, fields[0], &attr.name)
				if string(fields[1]) == "null" {
					event.attrs = append(event.attrs, attr)
					continue
				}
				var value string
				decodeTokenizeText(t, fields[1], &value)
				attr.value = &value
				event.attrs = append(event.attrs, attr)
			}
		}
		events = append(events, event)
	}
	return events
}

func decodeTokenizeText(t *testing.T, raw json.RawMessage, out *string) {
	t.Helper()
	var text pyText
	if err := text.UnmarshalJSON(raw); err != nil {
		t.Fatalf("bad text %s: %v", raw, err)
	}
	*out = text.value
}

// recordTokenize runs tokenize and records the callbacks in the same shape.
func recordTokenize(markup string) ([]tokenizeEvent, error) {
	var events []tokenizeEvent
	err := tokenize(pystr.Runes(markup), func(event parserEvent) error {
		recorded := tokenizeEvent{kind: event.kind, text: pystr.String(event.text)}
		for _, attr := range event.attrs {
			item := tokenizeAttr{name: pystr.String(attr.name)}
			if attr.hasValue {
				value := pystr.String(attr.value)
				item.value = &value
			}
			recorded.attrs = append(recorded.attrs, item)
		}
		events = append(events, recorded)
		return nil
	})
	return events, err
}

func describeTokenizeEvent(event tokenizeEvent) string {
	if len(event.attrs) == 0 {
		return fmt.Sprintf("%s %q", event.kind, event.text)
	}
	attrs := ""
	for i, attr := range event.attrs {
		if i > 0 {
			attrs += " "
		}
		if attr.value == nil {
			attrs += fmt.Sprintf("%s=None", attr.name)
			continue
		}
		attrs += fmt.Sprintf("%s=%q", attr.name, *attr.value)
	}
	return fmt.Sprintf("%s %q [%s]", event.kind, event.text, attrs)
}

// firstTokenizeDifference reports the first differing event of a case, or ""
// when the event lists agree.
func firstTokenizeDifference(want, got []tokenizeEvent) string {
	for i := range want {
		if i >= len(got) {
			return fmt.Sprintf("event %d: got nothing, want %s", i, describeTokenizeEvent(want[i]))
		}
		if !reflect.DeepEqual(want[i], got[i]) {
			return fmt.Sprintf("event %d:\n got %s\nwant %s", i, describeTokenizeEvent(got[i]), describeTokenizeEvent(want[i]))
		}
	}
	if len(want) < len(got) {
		return fmt.Sprintf("event %d: got %s, want nothing", len(want), describeTokenizeEvent(got[len(want)]))
	}
	return ""
}

// TestTokenizeGolden compares the tokenizer with the callbacks recorded from
// html.parser.HTMLParser(convert_charrefs=False) on CPython 3.13.16.
func TestTokenizeGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/full.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []tokenizeCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 4000 {
		t.Fatalf("golden corpus has %d cases, want at least 4000", len(cases))
	}
	reported, mismatches := 0, 0
	for i, c := range cases {
		want := decodeTokenizeEvents(t, c.Events)
		got, err := recordTokenize(c.Input.value)
		problem := ""
		switch {
		case c.EventsError == nil && err != nil:
			problem = fmt.Sprintf("tokenize returned %v, want no error", err)
		case c.EventsError != nil && err == nil:
			problem = fmt.Sprintf("tokenize returned no error, want %s", *c.EventsError)
		case c.EventsError != nil && *c.EventsError == "AssertionError" && err != errParserAssertion:
			problem = fmt.Sprintf("tokenize returned %v, want errParserAssertion", err)
		case c.EventsError != nil && *c.EventsError != "AssertionError":
			problem = fmt.Sprintf("tokenize returned %v, want %s", err, *c.EventsError)
		default:
			problem = firstTokenizeDifference(want, got)
		}
		if problem == "" {
			continue
		}
		mismatches++
		if reported < 25 {
			reported++
			t.Errorf("case %d (%s) input %q:\n%s", i, c.Kind, c.Input.value, problem)
		}
	}
	if mismatches > 0 {
		t.Fatalf("%d of %d cases mismatch", mismatches, len(cases))
	}
}
