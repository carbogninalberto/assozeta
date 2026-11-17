package pdfexport

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"os"
	"reflect"
	"strings"
	"testing"
)

// colourInfo is the tagged form generate_pdf_paragraphs.py writes for a colour
// attribute value: a number as its Python repr, a colour component as the repr
// of its float (what fp_str draws), a spotName nested.
func colourInfo(c *color) any {
	if c == nil {
		return nil
	}
	switch c.kind {
	case colorRGB:
		return map[string]any{"t": "rgb", "v": []any{pyFloatRepr(c.r), pyFloatRepr(c.g), pyFloatRepr(c.b)},
			"a": pyFloatRepr(c.alphaValue())}
	case colorCMYK:
		className := map[cmykClass]string{cmykPlain: "CMYKColor", cmykP: "PCMYKColor",
			cmykSep: "CMYKColorSep", cmykPSep: "PCMYKColorSep"}[c.class]
		return map[string]any{"t": "cmyk", "v": []any{pyFloatRepr(c.r), pyFloatRepr(c.g), pyFloatRepr(c.b)},
			"c": []any{pyFloatRepr(c.c), pyFloatRepr(c.m), pyFloatRepr(c.y), pyFloatRepr(c.k)},
			"d": pyFloatRepr(c.dens), "a": pyFloatRepr(c.alphaValue()), "s": colourInfo(c.spot), "cl": className}
	case colorTuple, colorList:
		items := make([]any, len(c.items))
		for i := range c.items {
			items[i] = colourInfo(&c.items[i])
		}
		kind := "tuple"
		if c.kind == colorList {
			kind = "list"
		}
		return map[string]any{"t": kind, "v": items}
	case colorStr:
		return map[string]any{"t": "str", "v": c.text}
	case colorBytes:
		return map[string]any{"t": "bytes", "v": c.text}
	case colorNone:
		return nil
	case colorInt:
		return map[string]any{"t": "num", "v": pyFloatRepr(colorNumber(*c))}
	case colorBool:
		return map[string]any{"t": "bool", "v": c.f != 0}
	case colorFloat:
		return map[string]any{"t": "num", "v": pyFloatRepr(colorNumber(*c))}
	case colorComplex:
		return map[string]any{"t": "complex", "v": []any{pyFloatRepr(c.f), pyFloatRepr(c.im)}}
	case colorDict:
		pairs := make([]any, len(c.pairs))
		for i := range c.pairs {
			pairs[i] = []any{colourInfo(&c.pairs[i].key), colourInfo(&c.pairs[i].value)}
		}
		return map[string]any{"t": "dict", "v": pairs}
	case colorEllipsis:
		return map[string]any{"t": "ellipsis"}
	}
	return map[string]any{"t": "opaque"}
}

func usValue(v usVal) any {
	if v.isNum {
		return numFloat(v.num).corpus()
	}
	return v.str
}

// corpus is the form generate_pdf_paragraphs.py writes for a number: its exact
// digits for an int, a JSON number for a float, and a marker string for a
// non-finite one.
func (n pyNum) corpus() any {
	if n.isInt() {
		if n.digits == "" {
			return json.Number("0")
		}
		return json.Number(n.digits)
	}
	switch {
	case math.IsNaN(n.f):
		return "nan"
	case math.IsInf(n.f, 1):
		return "inf"
	case math.IsInf(n.f, -1):
		return "-inf"
	}
	return n.f
}

// corpusValue is a described value with its pyNum numbers in corpus form.
func corpusValue(v any) any {
	switch x := v.(type) {
	case pyNum:
		return x.corpus()
	case []any:
		out := make([]any, len(x))
		for i := range x {
			out[i] = corpusValue(x[i])
		}
		return out
	}
	return v
}

// describeStyle is the form generate_pdf_paragraphs.py writes for a style.
func describeStyle(s paraStyle) []any {
	if !s.linesSet {
		s.lines = defaultLines()
	}
	lines := []any{}
	for _, l := range s.lines {
		var col *color
		if l.hasColor {
			col = &l.color
		}
		lines = append(lines, []any{usValue(l.width), usValue(l.offset), usValue(l.gap), colourInfo(col)})
	}
	return []any{s.fontName, s.fontSize.corpus(), s.leading.corpus(), s.alignment(), s.spaceBefore.corpus(), s.spaceAfter.corpus(),
		colourInfo(&s.textColor), colourInfo(s.backColor), lines, s.shrinkage().corpus(), !s.noSplit, !s.denyWidows, s.allowOrphans}
}

// describeFrag is the form generate_pdf_paragraphs.py writes for a fragment.
func describeFrag(f *frag) []any {
	lines := []any{}
	for _, l := range f.us {
		kind := "underline"
		if l.kind == lineStrike {
			kind = "strike"
		}
		var col *color
		if l.hasColor {
			col = &l.color
		}
		lines = append(lines, []any{l.n, kind, colourInfo(col), usValue(l.width), usValue(l.offset), l.rise.corpus(), l.mult, usValue(l.gap)})
	}
	links := []any{}
	for _, l := range f.links {
		links = append(links, []any{l.n, l.target})
	}
	var img any
	if f.anchor != nil {
		img = []any{"anchor", f.anchor.name}
	} else if f.img != nil {
		dimOf := func(d dim) any {
			if d.pct {
				return []any{"pct", d.v.corpus()}
			}
			return d.v.corpus()
		}
		va := f.img.valign
		var vaValue any = va.name
		switch {
		case va.pct:
			vaValue = []any{"pct", va.v.corpus()}
		case va.name == "":
			vaValue = va.v.corpus()
		}
		img = []any{"img", f.img.src, dimOf(f.img.width), dimOf(f.img.height), vaValue}
	}
	return []any{f.font.name, f.text, f.lineBreak, f.size.corpus(), colourInfo(&f.color), f.rise.corpus(), lines, links,
		colourInfo(f.back), f.nobr, img}
}

// corpusJSON is the JSON of a described value as the corpus reads it: every
// number is a json.Number, so an int of any size keeps its digits.
func corpusJSON(t *testing.T, value any) any {
	t.Helper()
	raw, err := json.Marshal(corpusValue(value))
	if err != nil {
		t.Fatalf("marshal %v: %v", value, err)
	}
	return decodeCorpus(t, raw)
}

func decodeCorpus(t *testing.T, raw []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", raw, err)
	}
	return value
}

// sameCorpus compares a value of the corpus with one of this package: numbers
// by exact decimal value (a Python int keeps its digits, a float its JSON
// number), everything else by equality.
func sameCorpus(want, have any) bool {
	switch w := want.(type) {
	case json.Number:
		h, ok := have.(json.Number)
		if !ok {
			return false
		}
		x, okx := new(big.Rat).SetString(w.String())
		y, oky := new(big.Rat).SetString(h.String())
		if okx && oky {
			return x.Cmp(y) == 0
		}
		return w.String() == h.String()
	case []any:
		h, ok := have.([]any)
		if !ok || len(w) != len(h) {
			return false
		}
		for i := range w {
			if !sameCorpus(w[i], h[i]) {
				return false
			}
		}
		return true
	default:
		return reflect.DeepEqual(want, have)
	}
}

// The numbers of the markup: the values reportlab 4.2.5 read for the same
// strings (CPython 3.13.16), the ValueError of int() and float() included.
func TestPythonNumbers(t *testing.T) {
	big := "1" + strings.Repeat("0", 399)
	ints := []struct{ text, want string }{
		{"12", "12"}, {" 12 ", "12"}, {"+12", "12"}, {"-12", "-12"}, {"1_000", "1000"}, {"١٢", "12"}, {"\u00a012", "12"},
		{"\u300012", "12"}, {"-0", "0"}, {"+0", "0"}, {big, big},
	}
	for _, item := range ints {
		got, st := pyInt(item.text, 10)
		if st != attrOK || got.String() != item.want {
			t.Errorf("int(%q) = %v, %v; want %s", item.text, got, st, item.want)
		}
	}
	for _, text := range []string{"1" + strings.Repeat("1", 4300), "\x1c12", "12x", "1__0", "_10", "10_", "½", "1e5", "", "  ", "0x1F"} {
		if _, st := pyInt(text, 10); st != attrInvalid {
			t.Errorf("int(%q) = %v; want a ValueError", text, st)
		}
	}
	hexes := []struct{ text, want string }{
		{"0x1F", "31"}, {"0x_1f", "31"}, {"0x1_f", "31"}, {"-0x1f", "-31"}, {"0b101", "45313"}, {"1_0", "16"},
		{"0X1F", "31"}, {" 0x1F ", "31"}, {"0x1f\n", "31"}, {strings.Repeat("f", 30), "1329227995784915872903807060280344575"},
	}
	for _, item := range hexes {
		got, st := pyInt(item.text, 16)
		if st != attrOK || got.String() != item.want {
			t.Errorf("int(%q, 16) = %v, %v; want %s", item.text, got, st, item.want)
		}
	}
	for _, text := range []string{"0_x1f", "0x", "0x_", ""} {
		if _, st := pyInt(text, 16); st != attrInvalid {
			t.Errorf("int(%q, 16) = %v; want a ValueError", text, st)
		}
	}
	floats := []struct {
		text string
		want float64
	}{
		{"inf", math.Inf(1)}, {"-Infinity", math.Inf(-1)}, {"+NaN", math.NaN()}, {"1e400", math.Inf(1)}, {"-1e400", math.Inf(-1)},
		{"1e-400", 0}, {"1e308", 1e308}, {".5", 0.5}, {"1.", 1}, {"1_0.5", 10.5}, {"1e1_0", 1e10}, {" ١٢ ", 12}, {"1_0", 10},
		{"1e+2", 100}, {"-.5", -0.5}, {"+1.5e-3", 0.0015},
	}
	for _, item := range floats {
		got, st := pyFloat(item.text)
		if st != attrOK {
			t.Errorf("float(%q) = %v; want %v", item.text, st, item.want)
			continue
		}
		if math.IsNaN(item.want) {
			if !math.IsNaN(got) {
				t.Errorf("float(%q) = %v; want NaN", item.text, got)
			}
			continue
		}
		if got != item.want {
			t.Errorf("float(%q) = %v; want %v", item.text, got, item.want)
		}
	}
	for _, text := range []string{"1_.5", "1._5", "1.5_", "0x1f", "12x", "\x1c12", "", "nan(1)", "1 2"} {
		if _, st := pyFloat(text); st != attrInvalid {
			t.Errorf("float(%q) = %v; want a ValueError", text, st)
		}
	}
}

// The corpus comes from reportlab's own Paragraph
// (TESTS/fixtures/generate_pdf_paragraphs.py): the fragments of a text, an
// exception, or fragments this package refuses.
func TestParagraphParserCorpus(t *testing.T) {
	raw, err := os.ReadFile("testdata/paragraphs.json")
	if err != nil {
		t.Fatal(err)
	}
	decoded := decodeCorpus(t, raw)
	file, ok := decoded.(map[string]any)
	if !ok {
		t.Fatal("the corpus is not an object")
	}
	items, ok := file["cases"].([]any)
	if !ok {
		t.Fatal("the corpus has no cases")
	}
	exact, failures := 0, 0
	// The corpus has a <seq/> case: reportlab numbered it on a fresh Sequencer.
	resetParaSequencer()
	for _, entry := range items {
		item := entry.(map[string]any)
		text, _ := item["text"].(string)
		font, _ := item["font"].(string)
		refusable := item["refusable"] == true
		other := item["other"] == true
		frags, style, err := parseParagraph(paraClean(text), paraStyle{fontName: font, fontSize: numInt(9), leading: numInt(12)})
		if errors.Is(err, ErrNotPorted) {
			if !refusable && !other {
				t.Errorf("%q: refused", text)
			}
			continue
		}
		switch {
		case item["build_error"] == true:
			// reportlab parses these (an <index> or onDraw callback); drawing fails the document, as here.
			if !errors.Is(err, ErrLayout) {
				t.Errorf("%q: the document cannot be built; got %d fragments, %v", text, len(frags), err)
			}
			failures++
		case other:
			t.Errorf("%q: reportlab makes fragments this package does not draw; got %v, %v", text, len(frags), err)
		case item["error"] == true:
			if !errors.Is(err, ErrLayout) {
				t.Errorf("%q: reportlab raises; got %d fragments, %v", text, len(frags), err)
			}
			failures++
		default:
			if err != nil {
				t.Errorf("%q: %v", text, err)
				continue
			}
			got := []any{}
			for _, f := range frags {
				got = append(got, describeFrag(f))
			}
			if !sameCorpus(item["style"], corpusJSON(t, describeStyle(style))) {
				wantStyle, _ := json.Marshal(item["style"])
				haveStyle, _ := json.Marshal(corpusValue(describeStyle(style)))
				t.Errorf("%q: style\nwant %s\n got %s", text, wantStyle, haveStyle)
			}
			if !sameCorpus(item["frags"], corpusJSON(t, got)) {
				want, _ := json.Marshal(item["frags"])
				have, _ := json.Marshal(corpusValue(got))
				t.Errorf("%q:\nwant %s\n got %s", text, want, have)
			}
			exact++
		}
	}
	if exact < 500 || failures < 500 {
		t.Fatalf("%d parsed and %d failing texts compared", exact, failures)
	}
}
