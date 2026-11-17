// Package pdfexport writes the PDF documents Django's exports build
// in-process with reportlab 4.2.5 (pure Python, no accelerator): the same
// objects in the same order with the same content stream operators, so that a
// document equals Django's byte for byte once the streams are decoded. What is
// free: the deflated bytes of the page streams (and the lengths and offsets
// that follow from them), the creation time and the /ID derived from it (see
// docs/architecture/go-migration/pdf-exports.md). The golden documents in
// testdata come from reportlab itself (TESTS/fixtures/generate_pdf_golden.py).
package pdfexport

import (
	"bytes"
	"compress/zlib"
	"crypto/md5"
	"encoding/ascii85"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrLayout is a document reportlab fails to build (LayoutError and the other
// exceptions of doc.build): callers answer what Django answers for an
// exception of the export.
var ErrLayout = errors.New("pdfexport: reportlab fails to build this document")

// LayoutError is such a failure with str(exception), which the agent's tool
// result carries ({"error": str(e)}); every LayoutError matches ErrLayout.
type LayoutError struct{ Text string }

func (e *LayoutError) Error() string { return e.Text }

// Is matches ErrLayout: callers that only answer a server error keep using
// errors.Is.
func (e *LayoutError) Is(target error) bool { return target == ErrLayout }

func layoutFailure(text string) error { return &LayoutError{Text: text} }

// ErrNotPorted is an input whose document this package does not reproduce:
// callers refuse it before any write.
var ErrNotPorted = refusal.New("pdfexport: document input not ported")

// ErrUnexpectedLayout is a document structure the builders never make: an
// auto-width table cell that is not a list of paragraphs (the one auto-width
// table, ReportTable, holds paragraph cells only), and a JPEG whose colour
// model or size image/jpeg never reports once it has decoded the data. It is
// an application-state error (Django's 500 page), never handed off.
var ErrUnexpectedLayout = refusal.AppState("pdfexport: unexpected document layout")

const fuzz = 1e-6 // rl_config._FUZZ

// fpStr is rl_accel.fp_str (the Python implementation) of one number.
func fpStr(value float64) string {
	sa := math.Abs(value)
	if sa <= 1e-7 {
		return "0"
	}
	l := 6
	if sa > 1 {
		l = 6 - int(math.Log10(sa))
		if l < 0 {
			l = 0
		}
		if l > 6 {
			l = 6
		}
	}
	n := strconv.FormatFloat(value, 'f', l, 64)
	if l > 0 {
		j := len(n)
		for j > 0 {
			j--
			if n[j] != '0' {
				if n[j] != '.' {
					j++
				}
				break
			}
		}
		n = n[:j]
	}
	if n[0] == '0' && len(n) != 1 {
		return n[1:]
	}
	return n
}

func fpStrs(values ...float64) string {
	parts := make([]string, len(values))
	for i, value := range values {
		parts[i] = fpStr(value)
	}
	return strings.Join(parts, " ")
}

// escapePDF is rl_accel.escapePDF of encoded bytes.
func escapePDF(data []byte) string {
	var out strings.Builder
	for _, c := range data {
		switch {
		case c < 32 || c >= 127:
			fmt.Fprintf(&out, "\\%03o", c)
		case c == '\\' || c == '(' || c == ')':
			out.WriteByte('\\')
			out.WriteByte(c)
		default:
			out.WriteByte(c)
		}
	}
	return out.String()
}

// pdfdocEncoding is the pdfdoc encoding of rl_codecs (PDFDocEncoding): each
// character the encoding holds and its byte.
var pdfdocEncoding = map[rune]byte{
	0x0000: 0x00,
	0x0001: 0x01,
	0x0002: 0x02,
	0x0003: 0x03,
	0x0004: 0x04,
	0x0005: 0x05,
	0x0006: 0x06,
	0x0007: 0x07,
	0x0008: 0x08,
	0x0009: 0x09,
	0x000A: 0x0A,
	0x000B: 0x0B,
	0x000C: 0x0C,
	0x000D: 0x0D,
	0x000E: 0x0E,
	0x000F: 0x0F,
	0x0010: 0x10,
	0x0011: 0x11,
	0x0012: 0x12,
	0x0013: 0x13,
	0x0014: 0x14,
	0x0015: 0x15,
	0x0016: 0x16,
	0x0017: 0x17,
	0x02D8: 0x18,
	0x02C7: 0x19,
	0x02C6: 0x1A,
	0x02D9: 0x1B,
	0x02DD: 0x1C,
	0x02DB: 0x1D,
	0x02DA: 0x1E,
	0x02DC: 0x1F,
	0x0020: 0x20,
	0x0021: 0x21,
	0x0022: 0x22,
	0x0023: 0x23,
	0x0024: 0x24,
	0x0025: 0x25,
	0x0026: 0x26,
	0x0027: 0x27,
	0x0028: 0x28,
	0x0029: 0x29,
	0x002A: 0x2A,
	0x002B: 0x2B,
	0x002C: 0x2C,
	0x002D: 0x2D,
	0x002E: 0x2E,
	0x002F: 0x2F,
	0x0030: 0x30,
	0x0031: 0x31,
	0x0032: 0x32,
	0x0033: 0x33,
	0x0034: 0x34,
	0x0035: 0x35,
	0x0036: 0x36,
	0x0037: 0x37,
	0x0038: 0x38,
	0x0039: 0x39,
	0x003A: 0x3A,
	0x003B: 0x3B,
	0x003C: 0x3C,
	0x003D: 0x3D,
	0x003E: 0x3E,
	0x003F: 0x3F,
	0x0040: 0x40,
	0x0041: 0x41,
	0x0042: 0x42,
	0x0043: 0x43,
	0x0044: 0x44,
	0x0045: 0x45,
	0x0046: 0x46,
	0x0047: 0x47,
	0x0048: 0x48,
	0x0049: 0x49,
	0x004A: 0x4A,
	0x004B: 0x4B,
	0x004C: 0x4C,
	0x004D: 0x4D,
	0x004E: 0x4E,
	0x004F: 0x4F,
	0x0050: 0x50,
	0x0051: 0x51,
	0x0052: 0x52,
	0x0053: 0x53,
	0x0054: 0x54,
	0x0055: 0x55,
	0x0056: 0x56,
	0x0057: 0x57,
	0x0058: 0x58,
	0x0059: 0x59,
	0x005A: 0x5A,
	0x005B: 0x5B,
	0x005C: 0x5C,
	0x005D: 0x5D,
	0x005E: 0x5E,
	0x005F: 0x5F,
	0x0060: 0x60,
	0x0061: 0x61,
	0x0062: 0x62,
	0x0063: 0x63,
	0x0064: 0x64,
	0x0065: 0x65,
	0x0066: 0x66,
	0x0067: 0x67,
	0x0068: 0x68,
	0x0069: 0x69,
	0x006A: 0x6A,
	0x006B: 0x6B,
	0x006C: 0x6C,
	0x006D: 0x6D,
	0x006E: 0x6E,
	0x006F: 0x6F,
	0x0070: 0x70,
	0x0071: 0x71,
	0x0072: 0x72,
	0x0073: 0x73,
	0x0074: 0x74,
	0x0075: 0x75,
	0x0076: 0x76,
	0x0077: 0x77,
	0x0078: 0x78,
	0x0079: 0x79,
	0x007A: 0x7A,
	0x007B: 0x7B,
	0x007C: 0x7C,
	0x007D: 0x7D,
	0x007E: 0x7E,
	0x007F: 0x7F,
	0x2022: 0x80,
	0x2020: 0x81,
	0x2021: 0x82,
	0x2026: 0x83,
	0x2014: 0x84,
	0x2013: 0x85,
	0x0192: 0x86,
	0x2044: 0x87,
	0x2039: 0x88,
	0x203A: 0x89,
	0x2212: 0x8A,
	0x2030: 0x8B,
	0x201E: 0x8C,
	0x201C: 0x8D,
	0x201D: 0x8E,
	0x2018: 0x8F,
	0x2019: 0x90,
	0x201A: 0x91,
	0x2122: 0x92,
	0xFB01: 0x93,
	0xFB02: 0x94,
	0x0141: 0x95,
	0x0152: 0x96,
	0x0160: 0x97,
	0x0178: 0x98,
	0x017D: 0x99,
	0x0131: 0x9A,
	0x0142: 0x9B,
	0x0153: 0x9C,
	0x0161: 0x9D,
	0x017E: 0x9E,
	0x009F: 0x9F,
	0x20AC: 0xA0,
	0x00A1: 0xA1,
	0x00A2: 0xA2,
	0x00A3: 0xA3,
	0x00A4: 0xA4,
	0x00A5: 0xA5,
	0x00A6: 0xA6,
	0x00A7: 0xA7,
	0x00A8: 0xA8,
	0x00A9: 0xA9,
	0x00AA: 0xAA,
	0x00AB: 0xAB,
	0x00AC: 0xAC,
	0x00AD: 0xAD,
	0x00AE: 0xAE,
	0x00AF: 0xAF,
	0x00B0: 0xB0,
	0x00B1: 0xB1,
	0x00B2: 0xB2,
	0x00B3: 0xB3,
	0x00B4: 0xB4,
	0x00B5: 0xB5,
	0x00B6: 0xB6,
	0x00B7: 0xB7,
	0x00B8: 0xB8,
	0x00B9: 0xB9,
	0x00BA: 0xBA,
	0x00BB: 0xBB,
	0x00BC: 0xBC,
	0x00BD: 0xBD,
	0x00BE: 0xBE,
	0x00BF: 0xBF,
	0x00C0: 0xC0,
	0x00C1: 0xC1,
	0x00C2: 0xC2,
	0x00C3: 0xC3,
	0x00C4: 0xC4,
	0x00C5: 0xC5,
	0x00C6: 0xC6,
	0x00C7: 0xC7,
	0x00C8: 0xC8,
	0x00C9: 0xC9,
	0x00CA: 0xCA,
	0x00CB: 0xCB,
	0x00CC: 0xCC,
	0x00CD: 0xCD,
	0x00CE: 0xCE,
	0x00CF: 0xCF,
	0x00D0: 0xD0,
	0x00D1: 0xD1,
	0x00D2: 0xD2,
	0x00D3: 0xD3,
	0x00D4: 0xD4,
	0x00D5: 0xD5,
	0x00D6: 0xD6,
	0x00D7: 0xD7,
	0x00D8: 0xD8,
	0x00D9: 0xD9,
	0x00DA: 0xDA,
	0x00DB: 0xDB,
	0x00DC: 0xDC,
	0x00DD: 0xDD,
	0x00DE: 0xDE,
	0x00DF: 0xDF,
	0x00E0: 0xE0,
	0x00E1: 0xE1,
	0x00E2: 0xE2,
	0x00E3: 0xE3,
	0x00E4: 0xE4,
	0x00E5: 0xE5,
	0x00E6: 0xE6,
	0x00E7: 0xE7,
	0x00E8: 0xE8,
	0x00E9: 0xE9,
	0x00EA: 0xEA,
	0x00EB: 0xEB,
	0x00EC: 0xEC,
	0x00ED: 0xED,
	0x00EE: 0xEE,
	0x00EF: 0xEF,
	0x00F0: 0xF0,
	0x00F1: 0xF1,
	0x00F2: 0xF2,
	0x00F3: 0xF3,
	0x00F4: 0xF4,
	0x00F5: 0xF5,
	0x00F6: 0xF6,
	0x00F7: 0xF7,
	0x00F8: 0xF8,
	0x00F9: 0xF9,
	0x00FA: 0xFA,
	0x00FB: 0xFB,
	0x00FC: 0xFC,
	0x00FD: 0xFD,
	0x00FE: 0xFE,
	0x00FF: 0xFF,
}

// pdfString is PDFString(text, escape=1).format of the document's unencrypted
// output: the bytes of the pdfdoc encoding, or UTF-16BE with its byte order
// mark when the text holds a character that encoding does not (rl_codecs).
func pdfString(text string) string {
	var data []byte
	if pdfdocEncodable(text) {
		for _, r := range text {
			data = append(data, pdfdocEncoding[r])
		}
	} else {
		data = append(data, 0xfe, 0xff)
		for _, r := range text {
			if r < 0x10000 {
				data = append(data, byte(r>>8), byte(r))
				continue
			}
			// str.encode('utf_16_be') writes an astral character as its surrogate pair.
			r -= 0x10000
			high, low := 0xd800+(r>>10), 0xdc00+(r&0x3ff)
			data = append(data, byte(high>>8), byte(high), byte(low>>8), byte(low))
		}
	}
	return "(" + escapePDF(data) + ")"
}

// pdfdocEncodable is pdfdoc._checkPdfdoc: no character of the text is outside
// the pdfdoc encoding.
func pdfdocEncodable(text string) bool {
	for _, r := range text {
		if _, ok := pdfdocEncoding[r]; !ok {
			return false
		}
	}
	return true
}

// --- fonts -----------------------------------------------------------------------------------------------------

type font struct {
	name     string
	widths   *[256]int
	encoding map[rune]byte
	subs     []*font
	ascent   float64
	descent  float64
}

var (
	fontZapfDingbats  = &font{name: "ZapfDingbats", widths: &zapfDingbatsWidths, encoding: zapfDingbatsEncoding}
	fontSymbol        = &font{name: "Symbol", widths: &symbolWidths, encoding: symbolEncoding}
	fontHelvetica     = &font{name: "Helvetica", widths: &helveticaWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 718, descent: -207}
	fontHelveticaBold = &font{name: "Helvetica-Bold", widths: &helveticaBoldWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 718, descent: -207}
	// The oblique fonts share the widths of the upright ones.
	fontHelveticaOblique     = &font{name: "Helvetica-Oblique", widths: &helveticaWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 718, descent: -207}
	fontHelveticaBoldOblique = &font{name: "Helvetica-BoldOblique", widths: &helveticaBoldWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 718, descent: -207}
	// The Courier and Times faces the markup can name (std_fonts_gen.go); every standard font falls back to Symbol
	// and ZapfDingbats for the characters its encoding lacks.
	fontCourier            = &font{name: "Courier", widths: &courierWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 629, descent: -157}
	fontCourierBold        = &font{name: "Courier-Bold", widths: &courierBoldWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 626, descent: -142}
	fontCourierOblique     = &font{name: "Courier-Oblique", widths: &courierObliqueWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 629, descent: -157}
	fontCourierBoldOblique = &font{name: "Courier-BoldOblique", widths: &courierBoldObliqueWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 626, descent: -142}
	fontTimesRoman         = &font{name: "Times-Roman", widths: &timesRomanWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 683, descent: -217}
	fontTimesBold          = &font{name: "Times-Bold", widths: &timesBoldWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 676, descent: -205}
	fontTimesItalic        = &font{name: "Times-Italic", widths: &timesItalicWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 683, descent: -205}
	fontTimesBoldItalic    = &font{name: "Times-BoldItalic", widths: &timesBoldItalicWidths, encoding: winAnsiEncoding, subs: []*font{fontSymbol, fontZapfDingbats}, ascent: 699, descent: -205}
	fontsByName            = map[string]*font{"Helvetica": fontHelvetica, "Helvetica-Bold": fontHelveticaBold, "Helvetica-Oblique": fontHelveticaOblique,
		"Helvetica-BoldOblique": fontHelveticaBoldOblique, "Symbol": fontSymbol, "ZapfDingbats": fontZapfDingbats,
		"Courier": fontCourier, "Courier-Bold": fontCourierBold, "Courier-Oblique": fontCourierOblique,
		"Courier-BoldOblique": fontCourierBoldOblique, "Times-Roman": fontTimesRoman, "Times-Bold": fontTimesBold,
		"Times-Italic": fontTimesItalic, "Times-BoldItalic": fontTimesBoldItalic}
	notdefChar byte = 'n' // every standard font falls back to ZapfDingbats 'n'
)

type run struct {
	font *font
	data []byte
}

// unicode2T1 is rl_accel.unicode2T1: the text as runs of the font and its
// substitution fonts, an unencodable stretch going to the next font and
// finally to the notdef character.
func unicode2T1(text []rune, f *font, subs []*font) []run {
	var out []run
	for len(text) > 0 {
		// str.encode: the first unencodable stretch [i0, il).
		i0 := -1
		for i, r := range text {
			if _, ok := f.encoding[r]; !ok {
				i0 = i
				break
			}
		}
		if i0 < 0 {
			out = append(out, run{f, encodeRunes(text, f)})
			break
		}
		il := i0 + 1
		for il < len(text) {
			if _, ok := f.encoding[text[il]]; ok {
				break
			}
			il++
		}
		if i0 > 0 {
			out = append(out, run{f, encodeRunes(text[:i0], f)})
		}
		if len(subs) > 0 {
			out = append(out, unicode2T1(text[i0:il], subs[0], subs[1:])...)
		} else {
			out = append(out, run{fontZapfDingbats, bytes.Repeat([]byte{notdefChar}, il-i0)})
		}
		text = text[il:]
	}
	return out
}

func encodeRunes(text []rune, f *font) []byte {
	out := make([]byte, len(text))
	for i, r := range text {
		out[i] = f.encoding[r]
	}
	return out
}

// stringWidth is pdfmetrics.stringWidth for a standard font.
func stringWidth(text string, f *font, size float64) float64 {
	total := 0
	for _, part := range unicode2T1([]rune(text), f, f.subs) {
		for _, c := range part.data {
			total += part.font.widths[c]
		}
	}
	return float64(total) * 0.001 * size
}

// --- colours ---------------------------------------------------------------------------------------------------

// color is a reportlab colour value; named marks the string 'black' default
// of a table cell, which never compares equal to a Color instance.
type color struct {
	r, g, b float64
	named   bool
	none    bool
}

var (
	colorBlack = color{}
	colorWhite = color{r: 1, g: 1, b: 1}
	colorGrey  = color{r: 128.0 / 255, g: 128.0 / 255, b: 128.0 / 255}
)

func hexColor(value uint32) color {
	return color{r: float64(value>>16&0xFF) / 255, g: float64(value>>8&0xFF) / 255, b: float64(value&0xFF) / 255}
}

// --- canvas and document ---------------------------------------------------------------------------------------

type resource struct {
	font  *font
	image *jpegImage
}

type jpegImage struct {
	name          string
	data          []byte
	width, height int
	colorSpace    string
	inverted      bool // DeviceCMYK with /Decode [ 1 0 1 0 1 0 1 0 ]
}

type canvasState struct {
	fontName    string
	fontSize    float64
	leading     float64
	lineWidth   float64
	strokeColor color // color{named: true} is the initial (0,0,0) tuple, which no Color equals
	matrix      [6]float64
}

type pageRecord struct {
	width, height float64
	content       []byte
	images        []string
	annotations   []int
}

// annotation is a link annotation: its rectangle in page space and either its
// action (a URI or file target) or the destination an internal link names.
type annotation struct {
	rect   [4]float64
	action map[string]string
	dest   *destination
}

// destination is pdfdoc.Destination: a bookmark name, and the page and position
// it is bound to at the moment the document is saved. A link and the anchor of
// its name share one destination, so a later anchor rebinds both.
type destination struct {
	name      string
	bound     bool
	page      int
	left, top float64
}

// annotationBase marks an annotation in canvas.sequence.
const annotationBase = 1 << 24

type canvas struct {
	pageW, pageH float64
	resources    []resource // in order of first use
	// sequence is the order in which reportlab numbers the objects: a resource (-index-1) when it is first used, a page
	// (index) when it is shown.
	sequence     []int
	fontNumbers  map[string]int
	imageNames   map[string]bool
	code         []string
	state        canvasState
	stack        []canvasState
	pages        []pageRecord
	pageImages   []string
	annotations  []annotation
	pageAnnots   []int
	destinations map[string]*destination
	// err is set by a flowable that meets an input this package does not
	// reproduce while it is laid out.
	err error
}

func newCanvas(width, height float64) *canvas {
	c := &canvas{pageW: width, pageH: height, fontNumbers: map[string]int{}, imageNames: map[string]bool{},
		destinations: map[string]*destination{}}
	c.initState()
	c.fontRef(fontHelvetica)
	return c
}

func (c *canvas) initState() {
	c.state = canvasState{fontName: "Helvetica", fontSize: 12, leading: 14.4, lineWidth: 1, strokeColor: color{named: true}, matrix: [6]float64{1, 0, 0, 1, 0, 0}}
}

// fontRef is doc.getInternalFontName: /F<n>, registering the font on first use.
func (c *canvas) fontRef(f *font) string {
	number, ok := c.fontNumbers[f.name]
	if !ok {
		number = len(c.fontNumbers) + 1
		c.fontNumbers[f.name] = number
		c.sequence = append(c.sequence, -len(c.resources)-1)
		c.resources = append(c.resources, resource{font: f})
	}
	return "/F" + strconv.Itoa(number)
}

func (c *canvas) add(line string) { c.code = append(c.code, line) }

func (c *canvas) saveState() {
	c.stack = append(c.stack, c.state)
	c.add("q")
}

func (c *canvas) restoreState() {
	c.state = c.stack[len(c.stack)-1]
	c.stack = c.stack[:len(c.stack)-1]
	c.add("Q")
}

func (c *canvas) translate(x, y float64) { c.transform(1, 0, 0, 1, x, y) }

func (c *canvas) scale(x, y float64) { c.transform(x, 0, 0, y, 0, 0) }

// transform is canvas.transform: a matrix that follows another one is
// multiplied into the line already written, from its printed numbers.
func (c *canvas) transform(a, b, cc, d, e, f float64) {
	m := c.state.matrix
	c.state.matrix = [6]float64{m[0]*a + m[2]*b, m[1]*a + m[3]*b, m[0]*cc + m[2]*d, m[1]*cc + m[3]*d, m[0]*e + m[2]*f + m[4], m[1]*e + m[3]*f + m[5]}
	if n := len(c.code); n > 0 && strings.HasSuffix(c.code[n-1], " cm") {
		fields := strings.Fields(c.code[n-1])
		if len(fields) == 7 {
			var m [6]float64
			for i := range m {
				m[i], _ = strconv.ParseFloat(fields[i], 64)
			}
			c.code[n-1] = fpStrs(m[0]*a+m[2]*b, m[1]*a+m[3]*b, m[0]*cc+m[2]*d, m[1]*cc+m[3]*d, m[0]*e+m[2]*f+m[4], m[1]*e+m[3]*f+m[5]) + " cm"
			return
		}
	}
	c.add(fpStrs(a, b, cc, d, e, f) + " cm")
}

// drawImage is canvas.drawImage(image, 0, 0, width, height) of a JPEG.
func (c *canvas) drawImage(image *jpegImage, width, height float64) {
	c.drawImageAt(image, 0, 0, width, height)
}

// drawImageAt is canvas.drawImage(image, x, y, width, height) of a JPEG.
func (c *canvas) drawImageAt(image *jpegImage, x, y, width, height float64) {
	if !c.imageNames[image.name] {
		c.imageNames[image.name] = true
		c.sequence = append(c.sequence, -len(c.resources)-1)
		c.resources = append(c.resources, resource{image: image})
	}
	c.pageImages = append(c.pageImages, image.name)
	c.saveState()
	c.translate(x, y)
	c.scale(width, height)
	c.add("/" + image.name + " Do")
	c.restoreState()
}

func (c *canvas) setFont(name string, size, leading float64) {
	c.state.fontName, c.state.fontSize, c.state.leading = name, size, leading
	c.add("BT " + c.fontRef(fontsByName[name]) + " " + fpStr(size) + " Tf " + fpStr(leading) + " TL ET")
}

// setFontDefault is canvas.setFont(name, size): the leading is size * 1.2.
func (c *canvas) setFontDefault(name string, size float64) { c.setFont(name, size, size*1.2) }

func (c *canvas) setFillColor(col color) { c.add(fpStrs(col.r, col.g, col.b) + " rg") }
func (c *canvas) setStrokeColor(col color) {
	c.state.strokeColor = col
	c.add(fpStrs(col.r, col.g, col.b) + " RG")
}

func (c *canvas) setLineWidth(w float64) {
	c.state.lineWidth = w
	c.add(fpStr(w) + " w")
}
func (c *canvas) setLineCap(v int)  { c.add(strconv.Itoa(v) + " J") }
func (c *canvas) setLineJoin(v int) { c.add(strconv.Itoa(v) + " j") }

func (c *canvas) line(x1, y1, x2, y2 float64) {
	c.add("n " + fpStrs(x1, y1) + " m " + fpStrs(x2, y2) + " l S")
}

// fillRect is canvas.rect(x, y, w, h, stroke=0, fill=1).
func (c *canvas) fillRect(x, y, w, h float64) { c.add("n " + fpStrs(x, y, w, h) + " re f*") }

// formatText is PDFTextObject._formatText for a standard font at the given
// size and leading.
func (c *canvas) formatText(text string, f *font, size, leading float64) string {
	var parts []string
	current := f
	for _, part := range unicode2T1([]rune(text), f, f.subs) {
		if part.font != current {
			parts = append(parts, c.fontRef(part.font)+" "+fpStr(size)+" Tf "+fpStr(leading)+" TL")
			current = part.font
		}
		parts = append(parts, "("+escapePDF(part.data)+") Tj")
	}
	if current != f {
		parts = append(parts, c.fontRef(f)+" "+fpStr(size)+" Tf "+fpStr(leading)+" TL")
	}
	return strings.Join(parts, " ")
}

func (c *canvas) currentFont() *font { return fontsByName[c.state.fontName] }

func (c *canvas) stringWidth(text string) float64 {
	return stringWidth(text, c.currentFont(), c.state.fontSize)
}

func (c *canvas) drawString(x, y float64, text string) {
	c.add("BT 1 0 0 1 " + fpStrs(x, y) + " Tm " + c.formatText(text, c.currentFont(), c.state.fontSize, c.state.leading) + " T* ET")
}

func (c *canvas) drawCentredString(x, y float64, text string) {
	c.drawString(x-0.5*c.stringWidth(text), y, text)
}

func (c *canvas) drawRightString(x, y float64, text string) {
	c.drawString(x-c.stringWidth(text), y, text)
}

func (c *canvas) showPage() {
	body := "1 0 0 1 0 0 cm  BT /F1 12 Tf 14.4 TL ET\n" + strings.Join(c.code, "\n") + "\n \n"
	if len(c.code) == 0 {
		body = "1 0 0 1 0 0 cm  BT /F1 12 Tf 14.4 TL ET\n \n"
	}
	c.sequence = append(c.sequence, len(c.pages))
	c.pages = append(c.pages, pageRecord{width: c.pageW, height: c.pageH, content: []byte(body), images: c.pageImages, annotations: c.pageAnnots})
	c.code = nil
	c.pageImages = nil
	c.pageAnnots = nil
	c.stack = nil
	c.initState()
}

// pdfDict is PDFDictionary.format: keys sorted, a line break every six entries.
func pdfDict(entries map[string]string) string {
	keys := make([]string, 0, len(entries))
	for key := range entries {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var parts []string
	for i, key := range keys {
		if i > 0 && i%6 == 0 {
			parts = append(parts, "\n ")
		}
		parts = append(parts, "/"+key+" "+entries[key])
	}
	return "<<\n" + strings.Join(parts, " ") + "\n>>"
}

// pdfArray is PDFArray.format: a line break every ten elements.
func pdfArray(items []string) string {
	var parts []string
	for i, item := range items {
		if i > 0 && i%10 == 0 {
			parts = append(parts, "\n ")
		}
		parts = append(parts, item)
	}
	return "[ " + strings.Join(parts, " ") + " ]"
}

func ascii85Encode(data []byte) []byte {
	out := make([]byte, ascii85.MaxEncodedLen(len(data)))
	n := ascii85.Encode(out, data)
	return append(out[:n], '~', '>')
}

func deflate(data []byte) []byte {
	var out bytes.Buffer
	w := zlib.NewWriter(&out)
	_, _ = w.Write(data)
	_ = w.Close()
	return out.Bytes()
}

// pythonTimeRepr is ascii(time.time()) of the moment, to the microsecond.
func pythonTimeRepr(moment time.Time) string {
	value := float64(moment.UnixMicro()) / 1e6
	text := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

// save is canvas.save(): the whole file. now is the creation time, written
// in UTC (Django's TIME_ZONE); an internal link whose destination no anchor
// bound is an exception of the export.
func (c *canvas) save(now time.Time) ([]byte, error) {
	now = now.UTC()
	const fontDict = 1
	number := fontDict
	resourceNumber := make([]int, len(c.resources))
	pageNumber := make([]int, len(c.pages))
	annotationNumber := make([]int, len(c.annotations))
	for _, item := range c.sequence {
		number++
		switch {
		case item < 0:
			resourceNumber[-item-1] = number
		case item >= annotationBase:
			annotationNumber[item-annotationBase] = number
		default:
			pageNumber[item] = number
		}
	}
	catalog, info, tree := number+1, number+2, number+3
	number += 3
	streamNumber := make([]int, len(c.pages))
	for i := range c.pages {
		number++
		streamNumber[i] = number
	}
	ref := func(n int) string { return strconv.Itoa(n) + " 0 R" }

	var out bytes.Buffer
	out.WriteString("%PDF-1.4\n%\x93\x8c\x8b\x9e ReportLab Generated PDF document http://www.reportlab.com\n")
	offsets := make([]int, 0, number)
	object := func(n int, body string) {
		offsets = append(offsets, out.Len())
		out.WriteString(strconv.Itoa(n) + " 0 obj\n" + body)
		if !strings.HasSuffix(body, "\n") {
			out.WriteByte('\n')
		}
		out.WriteString("endobj\n")
	}

	fonts := map[string]string{}
	xobjects := map[string]string{}
	for i, res := range c.resources {
		if res.font != nil {
			fonts["F"+strconv.Itoa(c.fontNumbers[res.font.name])] = ref(resourceNumber[i])
		} else {
			xobjects[res.image.name] = ref(resourceNumber[i])
		}
	}
	object(fontDict, pdfDict(fonts))
	kids := make([]string, len(c.pages))
	writeResource := func(i int, res resource) {
		if res.font != nil {
			entries := map[string]string{"BaseFont": "/" + res.font.name, "Name": "/F" + strconv.Itoa(c.fontNumbers[res.font.name]),
				"Subtype": "/Type1", "Type": "/Font"}
			if res.font.encoding != nil && res.font != fontSymbol && res.font != fontZapfDingbats {
				entries["Encoding"] = "/WinAnsiEncoding"
			}
			object(resourceNumber[i], pdfDict(entries))
			return
		}
		encoded := ascii85Encode(res.image.data)
		entries := map[string]string{
			"BitsPerComponent": "8", "ColorSpace": "/" + res.image.colorSpace, "Filter": "[ /ASCII85Decode /DCTDecode ]",
			"Height": strconv.Itoa(res.image.height), "Length": strconv.Itoa(len(encoded)), "Subtype": "/Image", "Type": "/XObject",
			"Width": strconv.Itoa(res.image.width)}
		if res.image.inverted {
			entries["Decode"] = "[ 1 0 1 0 1 0 1 0 ]"
		}
		object(resourceNumber[i], pdfDict(entries)+"\nstream\n"+string(encoded)+"endstream\n")
	}
	writePage := func(i int, page pageRecord) {
		resources := map[string]string{"Font": ref(fontDict), "ProcSet": "[ /PDF /Text /ImageB /ImageC /ImageI ]"}
		if len(page.images) > 0 {
			used := map[string]string{}
			for _, name := range page.images {
				used[name] = xobjects[name]
			}
			resources["XObject"] = pdfDict(used)
		}
		entries := map[string]string{
			"Contents": ref(streamNumber[i]), "MediaBox": pdfArray([]string{"0", "0", fpStr(page.width), fpStr(page.height)}),
			"Parent": ref(tree), "Resources": pdfDict(resources), "Rotate": "0", "Trans": pdfDict(nil), "Type": "/Page"}
		if len(page.annotations) > 0 {
			refs := make([]string, len(page.annotations))
			for j, index := range page.annotations {
				refs[j] = ref(annotationNumber[index])
			}
			entries["Annots"] = pdfArray(refs)
		}
		object(pageNumber[i], pdfDict(entries))
		kids[i] = ref(pageNumber[i])
	}
	for _, item := range c.sequence {
		switch {
		case item < 0:
			writeResource(-item-1, c.resources[-item-1])
		case item >= annotationBase:
			a := c.annotations[item-annotationBase]
			entries := map[string]string{"Border": "[ 0 0 0 ]",
				"Rect": pdfArray([]string{fpStr(a.rect[0]), fpStr(a.rect[1]), fpStr(a.rect[2]), fpStr(a.rect[3])}), "Subtype": "/Link",
				"Type": "/Annot"}
			if a.dest != nil {
				// LinkAnnotation.Dict: an empty Contents and the destination, whose page binds at save.
				if !a.dest.bound {
					return nil, layoutFailure(fmt.Sprintf("format not resolved, probably missing URL scheme or undefined destination target for '%s'", a.dest.name))
				}
				entries["Contents"] = "()"
				entries["Dest"] = pdfArray([]string{ref(pageNumber[a.dest.page]), "/XYZ", fpStr(a.dest.left), fpStr(a.dest.top), "0"})
			} else {
				entries["A"] = pdfDict(a.action)
			}
			object(annotationNumber[item-annotationBase], pdfDict(entries))
		default:
			writePage(item, c.pages[item])
		}
	}
	object(catalog, pdfDict(map[string]string{"PageMode": "/UseNone", "Pages": ref(tree), "Type": "/Catalog"}))
	date := fmt.Sprintf("(D:%04d%02d%02d%02d%02d%02d+00'00')", now.Year(), int(now.Month()), now.Day(), now.Hour(), now.Minute(), now.Second())
	object(info, pdfDict(map[string]string{
		"Author": `(\(anonymous\))`, "CreationDate": date, "Creator": `(\(unspecified\))`, "Keywords": "()", "ModDate": date,
		"Producer": "(ReportLab PDF Library - www.reportlab.com)", "Subject": `(\(unspecified\))`, "Title": `(\(anonymous\))`,
		"Trapped": "/False"}))
	object(tree, pdfDict(map[string]string{"Count": strconv.Itoa(len(c.pages)), "Kids": pdfArray(kids), "Type": "/Pages"}))
	for i, page := range c.pages {
		encoded := ascii85Encode(deflate(page.content))
		object(streamNumber[i], pdfDict(map[string]string{"Filter": "[ /ASCII85Decode /FlateDecode ]", "Length": strconv.Itoa(len(encoded))})+
			"\nstream\n"+string(encoded)+"endstream\n")
	}
	start := out.Len()
	fmt.Fprintf(&out, "xref\n0 %d\n0000000000 65535 f \n", number+1)
	for _, offset := range offsets {
		fmt.Fprintf(&out, "%010d 00000 n \n", offset)
	}
	digest := md5.Sum([]byte("a reportlab document" + pythonTimeRepr(now) + "(anonymous)(anonymous)(unspecified)"))
	id := hex.EncodeToString(digest[:])
	fmt.Fprintf(&out, "trailer\n<<\n/ID \n[<%s><%s>]\n%% ReportLab generated PDF document -- digest (http://www.reportlab.com)\n\n/Info %d 0 R\n/Root %d 0 R\n/Size %d\n>>\nstartxref\n%d\n%%%%EOF\n",
		id, id, info, catalog, number+1, start)
	return out.Bytes(), nil
}

// absolutePosition is canvas.absolutePosition.
func (c *canvas) absolutePosition(x, y float64) (float64, float64) {
	m := c.state.matrix
	return m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5]
}

// absRect is canvas._absRect(rect, relative=1): the bounding box of the
// rectangle in page space.
func (c *canvas) absRect(rect [4]float64) [4]float64 {
	xll, yll := c.absolutePosition(rect[0], rect[1])
	xur, yur := c.absolutePosition(rect[2], rect[3])
	xul, yul := c.absolutePosition(rect[0], rect[3])
	xlr, ylr := c.absolutePosition(rect[2], rect[1])
	xs := []float64{xll, xur, xul, xlr}
	ys := []float64{yll, yur, yul, ylr}
	return [4]float64{min(xs[0], xs[1], xs[2], xs[3]), min(ys[0], ys[1], ys[2], ys[3]), max(xs[0], xs[1], xs[2], xs[3]),
		max(ys[0], ys[1], ys[2], ys[3])}
}

// bookmarkReference is canvas._bookmarkReference: the destination of a name,
// created unbound on first use.
func (c *canvas) bookmarkReference(name string) *destination {
	dest, ok := c.destinations[name]
	if !ok {
		dest = &destination{name: name}
		c.destinations[name] = dest
	}
	return dest
}

// bookmarkPage is canvas.bookmarkPage(key, fit='XYZ', left=left, top=top,
// zoom=0): the destination bound to the page being drawn.
func (c *canvas) bookmarkPage(name string, left, top float64) {
	dest := c.bookmarkReference(name)
	dest.bound, dest.page, dest.left, dest.top = true, len(c.pages), left, top
}

// bookmarkHorizontal is canvas.bookmarkHorizontal(key, relativeX, relativeY):
// the position in page space of that point in the current transform.
func (c *canvas) bookmarkHorizontal(name string, relX, relY float64) {
	left, top := c.absolutePosition(relX, relY)
	c.bookmarkPage(name, left, top)
}

// linkURL is canvas.linkURL(url, rect, relative=1, kind=kind).
func (c *canvas) linkURL(target string, rect [4]float64, kind string) {
	a := annotation{rect: c.absRect(rect)}
	text := pdfString(target)
	a.action = map[string]string{"S": "/" + kind, "Type": "/Action"}
	if kind == "URI" {
		a.action["URI"] = text
	} else {
		a.action["F"] = text
		a.action["D"] = "[ 0 /XYZ null null null ]"
	}
	index := len(c.annotations)
	c.annotations = append(c.annotations, a)
	c.sequence = append(c.sequence, annotationBase+index)
	c.pageAnnots = append(c.pageAnnots, index)
}

// linkRect is canvas.linkRect("", destinationname, rect, relative=1): a link
// to a bookmark, whose destination may be bound later.
func (c *canvas) linkRect(name string, rect [4]float64) {
	a := annotation{rect: c.absRect(rect), dest: c.bookmarkReference(name)}
	index := len(c.annotations)
	c.annotations = append(c.annotations, a)
	c.sequence = append(c.sequence, annotationBase+index)
	c.pageAnnots = append(c.pageAnnots, index)
}
