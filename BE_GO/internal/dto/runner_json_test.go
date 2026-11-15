package dto

import (
	"encoding/binary"
	"math"
	"strings"
	"testing"
	"unicode/utf16"
)

// encoded renders text in a codec json's detect_encoding recognizes.
func encoded(text, codec string) []byte {
	switch codec {
	case "utf-8":
		return []byte(text)
	case "utf-8-sig":
		return append([]byte{0xef, 0xbb, 0xbf}, []byte(text)...)
	case "utf-16":
		return append([]byte{0xff, 0xfe}, utf16Bytes(text, binary.LittleEndian)...)
	case "utf-16-be":
		return append([]byte{0xfe, 0xff}, utf16Bytes(text, binary.BigEndian)...)
	case "utf-16-le-nobom":
		return utf16Bytes(text, binary.LittleEndian)
	case "utf-16-be-nobom":
		return utf16Bytes(text, binary.BigEndian)
	case "utf-32":
		raw := []byte{0xff, 0xfe, 0x00, 0x00}
		for _, r := range text {
			raw = binary.LittleEndian.AppendUint32(raw, uint32(r))
		}
		return raw
	case "utf-32-le-nobom":
		var raw []byte
		for _, r := range text {
			raw = binary.LittleEndian.AppendUint32(raw, uint32(r))
		}
		return raw
	case "utf-32-be-nobom":
		var raw []byte
		for _, r := range text {
			raw = binary.BigEndian.AppendUint32(raw, uint32(r))
		}
		return raw
	}
	panic("unknown codec " + codec)
}

func utf16Bytes(text string, order binary.ByteOrder) []byte {
	units := utf16.Encode([]rune(text))
	raw := make([]byte, 2*len(units))
	for i, unit := range units {
		order.PutUint16(raw[2*i:], unit)
	}
	return raw
}

// TestRunnerJSONDecodeEncodings is json.loads(bytes) over the codecs json's
// detect_encoding selects from the BOMs and the NUL pattern.
func TestRunnerJSONDecodeEncodings(t *testing.T) {
	text := `{"archives": [], "note": "àè €", "size": 12}`
	for _, codec := range []string{"utf-8", "utf-8-sig", "utf-16", "utf-16-be", "utf-16-le-nobom", "utf-16-be-nobom",
		"utf-32", "utf-32-le-nobom", "utf-32-be-nobom"} {
		value, decoded, refused := RunnerJSONDecode(encoded(text, codec))
		if refused || !decoded {
			t.Fatalf("%s: decoded=%v refused=%v", codec, decoded, refused)
		}
		note, found := lookupRunnerTestValue(value, "note")
		if !found || note != "àè €" {
			t.Fatalf("%s: note=%q found=%v", codec, note, found)
		}
	}
}

// lookupRunnerTestValue reads a key of the decoded dict regardless of the Go
// type the ordered object uses.
func lookupRunnerTestValue(value any, name string) (any, bool) {
	switch v := value.(type) {
	case interface{ Lookup(string) (any, bool) }:
		return v.Lookup(name)
	case map[string]any:
		item, ok := v[name]
		return item, ok
	}
	return nil, false
}

// TestRunnerJSONDecodeConstants is Python's NaN, Infinity and -Infinity
// literals: floats that json.dumps writes back as the same tokens.
func TestRunnerJSONDecodeConstants(t *testing.T) {
	value, decoded, refused := RunnerJSONDecode([]byte(`{"nan": NaN, "inf": Infinity, "neg": -Infinity, "ok": [1, 2.5]}`))
	if refused || !decoded {
		t.Fatalf("decoded=%v refused=%v", decoded, refused)
	}
	for name, want := range map[string]func(float64) bool{
		"nan": math.IsNaN,
		"inf": func(v float64) bool { return math.IsInf(v, 1) },
		"neg": func(v float64) bool { return math.IsInf(v, -1) },
	} {
		item, found := lookupRunnerTestValue(value, name)
		if !found {
			t.Fatalf("%s missing", name)
		}
		number, ok := item.(float64)
		if !ok || !want(number) {
			t.Fatalf("%s=%v", name, item)
		}
	}
	if !strings.Contains(string(PythonJSONDumpsCompact(value)), "NaN") {
		t.Fatal("dumps lost the literal")
	}
}

// TestRunnerJSONDecodeSurrogates keeps Python's lone surrogate escapes and
// CESU-8 code units as WTF-8, and their pair as the character it encodes.
func TestRunnerJSONDecodeSurrogates(t *testing.T) {
	for name, raw := range map[string][]byte{
		"escape":  []byte(`["\ud800"]`),
		"cesu-8":  append(append([]byte(`["`), 0xed, 0xa0, 0x80), []byte(`"]`)...),
		"utf-16":  {0xff, 0xfe, 0x5b, 0x00, 0x22, 0x00, 0x00, 0xd8, 0x22, 0x00, 0x5d, 0x00},
		"utf-16b": {0xfe, 0xff, 0x00, 0x5b, 0x00, 0x22, 0xd8, 0x00, 0x00, 0x22, 0x00, 0x5d},
		"utf-32":  {0x00, 0x00, 0xfe, 0xff, 0, 0, 0, 0x5b, 0, 0, 0, 0x22, 0, 0, 0xd8, 0x00, 0, 0, 0, 0x22, 0, 0, 0, 0x5d},
	} {
		if _, decoded, refused := RunnerJSONDecode(raw); !refused || decoded {
			t.Fatalf("%s: decoded=%v refused=%v", name, decoded, refused)
		}
	}
	value, decoded, refused := RunnerJSONDecode([]byte(`["\ud83d\ude00"]`))
	if refused || !decoded {
		t.Fatalf("pair: decoded=%v refused=%v", decoded, refused)
	}
	array, ok := value.([]any)
	if !ok || len(array) != 1 || array[0] != "😀" {
		t.Fatalf("pair: %v", value)
	}
	if !RenderableJSON(value) {
		t.Fatal("a paired escape renders")
	}
	if RenderableJSON([]any{"\xed\xa0\x80"}) {
		t.Fatal("a lone surrogate is not renderable")
	}
}

// TestRunnerJSONDecodeRefusals keeps deep nesting (Python's RecursionError
// depends on the live call stack) and reads the ValueErrors as unavailable.
func TestRunnerJSONDecodeRefusals(t *testing.T) {
	deep := strings.Repeat("[", 501) + strings.Repeat("]", 501)
	if _, decoded, refused := RunnerJSONDecode([]byte(deep)); !refused || decoded {
		t.Fatalf("deep: decoded=%v refused=%v", decoded, refused)
	}
	shallow := strings.Repeat("[", 500) + strings.Repeat("]", 500)
	if _, decoded, refused := RunnerJSONDecode([]byte(shallow)); refused || !decoded {
		t.Fatalf("shallow: decoded=%v refused=%v", decoded, refused)
	}
	for name, raw := range map[string][]byte{
		"invalid-utf8":  {'[', 0xff, ']'},
		"invalid-utf16": {0xff, 0xfe, 0x5b, 0x00, 0x22},
		"invalid-utf32": {0xff, 0xfe, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff},
		"json":          []byte(`[1,`),
		"integer-limit": append([]byte{'['}, append([]byte(strings.Repeat("9", 4301)), ']')...),
		"trailing":      []byte(`[1] x`),
		"empty":         {},
	} {
		if _, decoded, refused := RunnerJSONDecode(raw); decoded || refused {
			t.Fatalf("%s: decoded=%v refused=%v", name, decoded, refused)
		}
	}
}

// TestRenderableJSON is DRF's JSONRenderer over the values the runner replies
// decode to.
func TestRenderableJSON(t *testing.T) {
	if RenderableJSON([]any{math.NaN()}) || RenderableJSON([]any{math.Inf(1)}) {
		t.Fatal("a non-finite float fails the strict dumps")
	}
	if !RenderableJSON([]any{1, "x", nil, true}) {
		t.Fatal("plain values render")
	}
}
