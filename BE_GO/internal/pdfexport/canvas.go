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

// ErrNotPorted is an input whose document this package does not reproduce:
// callers refuse it before any write.
var ErrNotPorted = refusal.New("pdfexport: document input not ported")

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
	fontsByName              = map[string]*font{"Helvetica": fontHelvetica, "Helvetica-Bold": fontHelveticaBold, "Helvetica-Oblique": fontHelveticaOblique,
		"Helvetica-BoldOblique": fontHelveticaBoldOblique, "Symbol": fontSymbol, "ZapfDingbats": fontZapfDingbats}
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
	fontName string
	fontSize float64
	leading  float64
}

type pageRecord struct {
	width, height float64
	content       []byte
	images        []string
}

type canvas struct {
	pageW, pageH float64
	resources    []resource // in order of first use
	// sequence is the order in which reportlab numbers the objects: a resource (-index-1) when it is first used, a page
	// (index) when it is shown.
	sequence    []int
	fontNumbers map[string]int
	imageNames  map[string]bool
	code        []string
	state       canvasState
	stack       []canvasState
	pages       []pageRecord
	pageImages  []string
	// err is set by a flowable that meets an input this package does not
	// reproduce while it is laid out.
	err error
}

func newCanvas(width, height float64) *canvas {
	c := &canvas{pageW: width, pageH: height, fontNumbers: map[string]int{}, imageNames: map[string]bool{}}
	c.initState()
	c.fontRef(fontHelvetica)
	return c
}

func (c *canvas) initState() {
	c.state = canvasState{fontName: "Helvetica", fontSize: 12, leading: 14.4}
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
	if !c.imageNames[image.name] {
		c.imageNames[image.name] = true
		c.sequence = append(c.sequence, -len(c.resources)-1)
		c.resources = append(c.resources, resource{image: image})
	}
	c.pageImages = append(c.pageImages, image.name)
	c.saveState()
	c.translate(0, 0)
	c.scale(width, height)
	c.add("/" + image.name + " Do")
	c.restoreState()
}

func (c *canvas) setFont(name string, size, leading float64) {
	c.state = canvasState{fontName: name, fontSize: size, leading: leading}
	c.add("BT " + c.fontRef(fontsByName[name]) + " " + fpStr(size) + " Tf " + fpStr(leading) + " TL ET")
}

// setFontDefault is canvas.setFont(name, size): the leading is size * 1.2.
func (c *canvas) setFontDefault(name string, size float64) { c.setFont(name, size, size*1.2) }

func (c *canvas) setFillColor(col color)   { c.add(fpStrs(col.r, col.g, col.b) + " rg") }
func (c *canvas) setStrokeColor(col color) { c.add(fpStrs(col.r, col.g, col.b) + " RG") }
func (c *canvas) setLineWidth(w float64)   { c.add(fpStr(w) + " w") }
func (c *canvas) setLineCap(v int)         { c.add(strconv.Itoa(v) + " J") }
func (c *canvas) setLineJoin(v int)        { c.add(strconv.Itoa(v) + " j") }

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
	c.pages = append(c.pages, pageRecord{width: c.pageW, height: c.pageH, content: []byte(body), images: c.pageImages})
	c.code = nil
	c.pageImages = nil
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
// in UTC (Django's TIME_ZONE).
func (c *canvas) save(now time.Time) []byte {
	now = now.UTC()
	const fontDict = 1
	number := fontDict
	resourceNumber := make([]int, len(c.resources))
	pageNumber := make([]int, len(c.pages))
	for _, item := range c.sequence {
		number++
		if item < 0 {
			resourceNumber[-item-1] = number
		} else {
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
		object(pageNumber[i], pdfDict(map[string]string{
			"Contents": ref(streamNumber[i]), "MediaBox": pdfArray([]string{"0", "0", fpStr(page.width), fpStr(page.height)}),
			"Parent": ref(tree), "Resources": pdfDict(resources), "Rotate": "0", "Trans": pdfDict(nil), "Type": "/Page"}))
		kids[i] = ref(pageNumber[i])
	}
	for _, item := range c.sequence {
		if item < 0 {
			writeResource(-item-1, c.resources[-item-1])
		} else {
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
	return out.Bytes()
}
