package pdfexport

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// The values a colour attribute of the markup can hold. reportlab's toColor
// (reportlab/lib/colors.py) answers a Color or CMYKColor instance, or whatever
// rl_extended_literal_eval returns as it is: a tuple or list of components, a
// string, a number, None, a dict and so on. The zero value is the Color
// reportlab calls black, the default text colour.
type colorKind uint8

const (
	colorRGB      colorKind = iota // a Color instance
	colorCMYK                      // a CMYKColor, PCMYKColor, CMYKColorSep or PCMYKColorSep instance
	colorTuple                     // a Python tuple
	colorList                      // a Python list
	colorStr                       // a Python str
	colorBytes                     // a Python bytes
	colorNone                      // Python None
	colorInt                       // a Python int
	colorBool                      // a Python bool
	colorFloat                     // a Python float
	colorComplex                   // a Python complex
	colorDict                      // a Python dict
	colorEllipsis                  // Python Ellipsis
	colorOpaque                    // another Python value, compared by key
)

// cmykClass is the class of a CMYK colour, which decides how reportlab reads
// its arguments and whether setFillColor writes a separation.
type cmykClass uint8

const (
	cmykPlain cmykClass = iota // CMYKColor
	cmykP                      // PCMYKColor
	cmykSep                    // CMYKColorSep
	cmykPSep                   // PCMYKColorSep
)

// color is a colour attribute value. Its components are the numbers
// reportlab keeps, so that equality follows Color.__eq__ and
// CMYKColor.__eq__: kind decides whether two values are comparable at all and
// the class of a CMYK colour does not take part.
type color struct {
	kind     colorKind
	r, g, b  float64 // the Color components; the RGB approximation of a CMYK colour
	alpha    float64
	hasAlpha bool
	c, m, y  float64 // a CMYK colour's components, as CMYKColor keeps them
	k, dens  float64
	spot     *color // spotName of a CMYK colour: nil is Python None
	class    cmykClass
	items    []color // the elements of a tuple or list
	pairs    []colorPair
	text     string // a str or bytes value
	num      *big.Int
	f, im    float64 // a float or complex value
	key      string  // an opaque value
}

// colorPair is an entry of a dict value.
type colorPair struct {
	key, value color
}

// alphaValue is Color.alpha, which is 1 when the value carries none.
func (c color) alphaValue() float64 {
	if c.hasAlpha {
		return c.alpha
	}
	return 1
}

// rgbColor is Color(red, green, blue) with the alpha reportlab defaults to 1.
func rgbColor(r, g, b float64) color { return color{r: r, g: g, b: b} }

// rgbColorA is Color(red, green, blue, alpha).
func rgbColorA(r, g, b, alpha float64) color {
	return color{r: r, g: g, b: b, alpha: alpha, hasAlpha: true}
}

// cmyk2rgbTuple is cmyk2rgb of a CMYK tuple, with Python's min over NaN.
func cmyk2rgbTuple(c, m, y, k float64) (float64, float64, float64) {
	return 1 - pyMin(1, c+k), 1 - pyMin(1, m+k), 1 - pyMin(1, y+k)
}

// newCMYKColor is the body of CMYKColor.__init__ (and of its subclasses):
// the density forced into [0, 1] and the RGB approximation adjusted by it.
func newCMYKColor(c, m, y, k, density float64, spot *color, alpha float64, class cmykClass) color {
	density = pyMax(pyMin(density, 1), 0)
	out := color{kind: colorCMYK, c: c, m: m, y: y, k: k, dens: density, spot: spot,
		alpha: alpha, hasAlpha: true, class: class}
	out.r, out.g, out.b = cmyk2rgbTuple(c, m, y, k)
	if density < 1 {
		out.r = density*(out.r-1) + 1
		out.g = density*(out.g-1) + 1
		out.b = density*(out.b-1) + 1
	}
	return out
}

// pyMinSeq is min(sequence) over numbers.
func pyMinSeq(values []float64) float64 {
	out := values[0]
	for _, v := range values[1:] {
		if v < out {
			out = v
		}
	}
	return out
}

// pyMaxSeq is max(sequence) over numbers.
func pyMaxSeq(values []float64) float64 {
	out := values[0]
	for _, v := range values[1:] {
		if v > out {
			out = v
		}
	}
	return out
}

func isNumberColor(c color) bool {
	switch c.kind {
	case colorInt, colorBool, colorFloat, colorComplex:
		return true
	}
	return false
}

// colorNumber is the real value of a number, or NaN for a complex without a
// real part.
func colorNumber(c color) float64 {
	switch c.kind {
	case colorInt:
		if c.num == nil {
			return 0
		}
		f, _ := new(big.Float).SetInt(c.num).Float64()
		return f
	case colorBool:
		if c.f != 0 {
			return 1
		}
		return 0
	case colorComplex:
		return c.f
	}
	return c.f
}

func colorComplexParts(c color) (float64, float64) {
	switch c.kind {
	case colorComplex:
		return c.f, c.im
	case colorInt, colorBool, colorFloat:
		return colorNumber(c), 0
	}
	return math.NaN(), math.NaN()
}

// spotEq is == of two spotName values, nil being None.
func spotEq(a, b *color) bool {
	if a == nil || b == nil {
		return a == b
	}
	return colorEq(*a, *b)
}

// colorEq is == of two colour attribute values. Colour.__eq__ compares two
// Color instances (never a CMYKColor), CMYKColor.__eq__ two CMYK colours by
// components, density, spot name and alpha, and the other values are Python
// values: numbers compare across int, bool, float and complex.
func colorEq(a, b color) bool {
	if isNumberColor(a) && isNumberColor(b) {
		if a.kind == colorComplex || b.kind == colorComplex {
			ar, ai := colorComplexParts(a)
			br, bi := colorComplexParts(b)
			return ar == br && ai == bi
		}
		return colorNumber(a) == colorNumber(b)
	}
	if a.kind != b.kind {
		return false
	}
	switch a.kind {
	case colorRGB:
		return a.r == b.r && a.g == b.g && a.b == b.b && a.alphaValue() == b.alphaValue()
	case colorCMYK:
		if a.c != b.c || a.m != b.m || a.y != b.y || a.k != b.k || a.dens != b.dens {
			return false
		}
		if !spotEq(a.spot, b.spot) {
			return false
		}
		return a.alphaValue() == b.alphaValue()
	case colorTuple, colorList:
		if len(a.items) != len(b.items) {
			return false
		}
		for i := range a.items {
			if !colorEq(a.items[i], b.items[i]) {
				return false
			}
		}
		return true
	case colorStr, colorBytes:
		return a.text == b.text
	case colorNone, colorEllipsis:
		return true
	case colorDict:
		if len(a.pairs) != len(b.pairs) {
			return false
		}
		for i := range a.pairs {
			found := false
			for j := range b.pairs {
				if colorEq(a.pairs[i].key, b.pairs[j].key) {
					found = colorEq(a.pairs[i].value, b.pairs[j].value)
					break
				}
			}
			if !found {
				return false
			}
		}
		return true
	case colorOpaque:
		return a.key == b.key
	}
	return false
}

// colorTruthy is bool(value): an empty sequence, an empty string, an empty
// dict, None and zero are false; everything else is true.
func colorTruthy(c color) bool {
	switch c.kind {
	case colorNone:
		return false
	case colorTuple, colorList:
		return len(c.items) > 0
	case colorDict:
		return len(c.pairs) > 0
	case colorStr, colorBytes:
		return c.text != ""
	case colorInt:
		if c.num == nil {
			return false
		}
		return c.num.Sign() != 0
	case colorBool, colorFloat:
		return c.f != 0
	case colorComplex:
		return c.f != 0 || c.im != 0
	}
	return true
}

// colorKey is a key that separates the values colorEq tells apart, for the
// maps and sets reportlab keys by colour.
func colorKey(c color) string {
	var out strings.Builder
	appendColorKey(&out, c)
	return out.String()
}

func appendColorKey(out *strings.Builder, c color) {
	switch c.kind {
	case colorRGB:
		out.WriteString("rgb:")
		appendFloatKey(out, c.r)
		appendFloatKey(out, c.g)
		appendFloatKey(out, c.b)
		appendFloatKey(out, c.alphaValue())
	case colorCMYK:
		out.WriteString("cmyk:")
		appendFloatKey(out, c.c)
		appendFloatKey(out, c.m)
		appendFloatKey(out, c.y)
		appendFloatKey(out, c.k)
		appendFloatKey(out, c.dens)
		appendFloatKey(out, c.alphaValue())
		out.WriteString("s")
		if c.spot != nil {
			appendColorKey(out, *c.spot)
		}
	case colorTuple, colorList:
		if c.kind == colorTuple {
			out.WriteString("tuple:")
		} else {
			out.WriteString("list:")
		}
		for _, item := range c.items {
			appendColorKey(out, item)
			out.WriteByte(',')
		}
	case colorStr:
		out.WriteString("str:")
		out.WriteString(c.text)
	case colorBytes:
		out.WriteString("bytes:")
		out.WriteString(c.text)
	case colorNone:
		out.WriteString("none")
	case colorEllipsis:
		out.WriteString("ellipsis")
	case colorInt, colorBool, colorFloat:
		out.WriteString("num:")
		appendFloatKey(out, colorNumber(c))
	case colorComplex:
		out.WriteString("complex:")
		appendFloatKey(out, c.f)
		appendFloatKey(out, c.im)
	case colorDict:
		out.WriteString("dict:")
		for _, item := range c.pairs {
			appendColorKey(out, item.key)
			out.WriteByte('=')
			appendColorKey(out, item.value)
			out.WriteByte(',')
		}
	case colorOpaque:
		out.WriteString("opaque:")
		out.WriteString(c.key)
	}
}

func appendFloatKey(out *strings.Builder, value float64) {
	out.WriteString(strconv.FormatFloat(normalizeZero(value), 'x', -1, 64))
	out.WriteByte(';')
}

// normalizeZero makes -0.0 the 0.0 Python compares it equal to.
func normalizeZero(value float64) float64 {
	if value == 0 {
		return 0
	}
	return value
}

// colorClassName is type(value).__name__, which linearlyInterpolatedColor
// compares.
func colorClassName(c color) string {
	switch c.kind {
	case colorRGB:
		return "Color"
	case colorCMYK:
		switch c.class {
		case cmykP:
			return "PCMYKColor"
		case cmykSep:
			return "CMYKColorSep"
		case cmykPSep:
			return "PCMYKColorSep"
		}
		return "CMYKColor"
	case colorTuple:
		return "tuple"
	case colorList:
		return "list"
	case colorStr:
		return "str"
	case colorBytes:
		return "bytes"
	case colorNone:
		return "NoneType"
	case colorInt:
		return "int"
	case colorBool:
		return "bool"
	case colorFloat:
		return "float"
	case colorComplex:
		return "complex"
	case colorDict:
		return "dict"
	case colorEllipsis:
		return "ellipsis"
	}
	return "object"
}

// colorObjectKey is a key of an opaque Python object: equal objects share it
// where the port compares values.
func colorObjectKey(kind, name string) color {
	return color{kind: colorOpaque, key: kind + ":" + name}
}

// intColor is a Python int value.
func intColor(n *big.Int) color { return color{kind: colorInt, num: n} }

func floatColor(f float64) color { return color{kind: colorFloat, f: f} }

func boolColor(b bool) color {
	out := color{kind: colorBool}
	if b {
		out.f = 1
	}
	return out
}

// strColor is a Python str value.
func strColor(s string) color { return color{kind: colorStr, text: s} }

// seqColor is a Python tuple or list of values.
func seqColor(items []color, list bool) color {
	kind := colorTuple
	if list {
		kind = colorList
	}
	return color{kind: kind, items: items}
}

// tupleColor is a Python tuple of numbers.
func tupleColor(values ...float64) color {
	items := make([]color, len(values))
	for i, value := range values {
		items[i] = floatColor(value)
	}
	return seqColor(items, false)
}

// --- drawing colours ------------------------------------------------------------------------------------------------

// separation is the colour space reportlab writes when a CMYKColorSep is
// drawn: /Separation /name /DeviceCMYK with the tint transfer function.
type separation struct {
	name string // the escaped PDF name, without the leading slash
	spot string // the name PDFName escapes, for the colour space array
	cmyk [4]float64
}

// cmykSpotName is the spotName of a CMYK colour as the text PDFName escapes:
// None and the other false values answer false, and a string is itself. Any
// other value is an exception of the export: PDFName of a non-string, or the
// resource dictionary of a name whose key is not a string.
func cmykSpotName(spot *color) (string, bool) {
	if spot == nil || !colorTruthy(*spot) {
		return "", false
	}
	if spot.kind != colorStr {
		panic(paraFailure{"PDFName of a non-character"})
	}
	return spot.text, true
}

// pdfName is PDFName(data): the characters outside 0x21..0x7e and the
// delimiter set escaped as #hh.
func pdfName(data string) string {
	var out strings.Builder
	for _, r := range data {
		if r < 0x21 || r > 0x7e || strings.ContainsRune("%()<>{}[]#", r) {
			out.WriteByte('#')
			out.WriteString(strconv.FormatInt(int64(r), 16))
			continue
		}
		out.WriteRune(r)
	}
	return "/" + out.String()
}

// separationFunction is PDFSeparationCMYKColor._makeFuncPS: the PostScript of
// the tint transfer function, which multiplies the tint by each component.
func separationFunction(cmyk [4]float64) string {
	var parts []string
	for i, v := range cmyk {
		if i == 3 {
			if v == 0 {
				parts = append(parts, "pop", "0.0")
			} else {
				parts = append(parts, pyFloatRepr(v), "mul")
			}
		} else {
			if v == 0 {
				parts = append(parts, "0.0")
			} else {
				parts = append(parts, "dup", pyFloatRepr(v), "mul")
			}
			parts = append(parts, "exch")
		}
	}
	return "{" + strings.Join(parts, " ") + "}"
}

// pyFloatRepr is repr() of a float: the shortest form that reads back, with an
// exponent when the decimal exponent is below -4 or at least 16.
func pyFloatRepr(value float64) string {
	switch {
	case math.IsInf(value, 1):
		return "inf"
	case math.IsInf(value, -1):
		return "-inf"
	case math.IsNaN(value):
		return "nan"
	}
	scientific := strconv.FormatFloat(value, 'e', -1, 64)
	mantissa, exponent, _ := strings.Cut(scientific, "e")
	exp, _ := strconv.Atoi(exponent)
	if exp < -4 || exp >= 16 {
		sign := "+"
		if exp < 0 {
			sign = "-"
			exp = -exp
		}
		mantissa = strings.TrimSuffix(mantissa, ".")
		return mantissa + "e" + sign + string(pad2(exp))
	}
	out := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(out, ".") {
		out += ".0"
	}
	return out
}

func pad2(n int) []byte {
	if n < 10 {
		return []byte{'0', byte('0' + n)}
	}
	return []byte(strconv.Itoa(n))
}

// --- reportlab's colour functions ----------------------------------------------------------------------------------

// toColorResult says whether toColor answers a value or raises. A "soft"
// failure is a ValueError or SyntaxError, which the caller of a nested toColor
// swallows; any other exception is a "hard" failure that fails the paragraph.
type toColorResult = evalResult

const (
	colorOK   = evalOK
	colorSoft = evalSoft
	colorHard = evalHard
)

// setFillColor is canvas.setFillColor(aColor) and textobject.setFillColor:
// the colour operators reportlab writes, then the alpha through the
// ExtGState. code receives the operators.
func (c *canvas) setColorOperators(col color, stroke bool, code *[]string) {
	switch col.kind {
	case colorRGB:
		op := "rg"
		if stroke {
			op = "RG"
		}
		*code = append(*code, fpStrs(col.r, col.g, col.b)+" "+op)
		c.setAlphaOperator(col.alphaValue(), stroke, code)
	case colorCMYK:
		d := col.dens
		if col.class == cmykSep || col.class == cmykPSep {
			name := c.addColorSpace(col)
			if stroke {
				*code = append(*code, "/"+name+" CS "+fpStr(d)+" SCN")
			} else {
				*code = append(*code, "/"+name+" cs "+fpStr(d)+" scn")
			}
		} else {
			op := "k"
			if stroke {
				op = "K"
			}
			*code = append(*code, fpStrs(d*col.c, d*col.m, d*col.y, d*col.k)+" "+op)
		}
		c.setAlphaOperator(col.alphaValue(), stroke, code)
	case colorTuple, colorList:
		var values []float64
		for _, item := range col.items {
			if !isNumberColor(item) || item.kind == colorComplex {
				panic(paraFailure{"a colour component is not a number"})
			}
			values = append(values, colorNumber(item))
		}
		switch len(values) {
		case 3:
			op := "rg"
			if stroke {
				op = "RG"
			}
			*code = append(*code, fpStrs(values...)+" "+op)
		case 4:
			op := "k"
			if stroke {
				op = "K"
			}
			*code = append(*code, fpStrs(values...)+" "+op)
		default:
			panic(paraFailure{"Unknown color"})
		}
	case colorStr:
		inner, res := paraColor(col.text)
		if res != attrOK {
			panic(paraFailure{"Invalid color value " + pyReprStr(col.text)})
		}
		c.setColorOperators(inner, stroke, code)
	default:
		panic(paraFailure{"Unknown color"})
	}
}

// setAlphaOperator is _PDFColorSetter.setFillAlpha / setStrokeAlpha: the
// value is checked, then an ExtGState carrying it is named and selected.
func (c *canvas) setAlphaOperator(alpha float64, stroke bool, code *[]string) {
	if !(alpha >= 0 && alpha <= 1) {
		panic(paraFailure{"setFillAlpha invalid value"})
	}
	if stroke {
		c.setExtGState("CA", alpha, code)
		return
	}
	c.setExtGState("ca", alpha, code)
}

// setExtGState is ExtGState.set: a value that differs from the state writes
// /gRLs<n> gs, the name of the (key, value) pair in the page's ExtGState.
func (c *canvas) setExtGState(op string, value float64, code *[]string) {
	if c.gsValue(op) == value {
		return
	}
	c.setGsValue(op, value)
	key := op + ":" + strconv.FormatFloat(value, 'g', -1, 64)
	name, ok := c.gsNames[key]
	if !ok {
		name = "gRLs" + strconv.Itoa(len(c.gsNames))
		c.gsNames[key] = name
		c.pageGStates = append(c.pageGStates, extGStateUse{name: name, op: op, value: value})
	}
	*code = append(*code, "/"+name+" gs")
}

// addColorSpace is PDFDocument.addColor and _checkSeparation: the name of the
// separation colour space of a spot colour, registered on first use.
func (c *canvas) addColorSpace(col color) string {
	spot, ok := cmykSpotName(col.spot)
	if !ok {
		switch {
		case col.c == 0 && col.m == 0 && col.y == 0:
			spot = "BLACK"
		case col.k == 0 && col.m == 0 && col.y == 0:
			spot = "CYAN"
		case col.c == 0 && col.k == 0 && col.y == 0:
			spot = "MAGENTA"
		case col.c == 0 && col.m == 0 && col.k == 0:
			spot = "YELLOW"
		default:
			panic(paraFailure{"CMYK colour used without a spotName"})
		}
	}
	name := pdfName(spot)[1:]
	if _, ok := c.separationIndex[name]; !ok {
		index := len(c.resources)
		c.separationIndex[name] = index
		c.sequence = append(c.sequence, -index-1)
		c.resources = append(c.resources, resource{separation: &separation{name: name, spot: spot,
			cmyk: [4]float64{col.c, col.m, col.y, col.k}}})
	}
	if !c.colorsUsed[name] {
		c.colorsUsed[name] = true
		c.pageColors = append(c.pageColors, name)
	}
	return name
}

// --- documented helpers ---------------------------------------------------------------------------------------------

// pyReprStr is repr() of a str, for the messages of the failures the port
// only records as a reason.
func pyReprStr(s string) string {
	quote := "'"
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = "\""
	}
	var out strings.Builder
	out.WriteString(quote)
	for _, r := range s {
		switch r {
		case '\\':
			out.WriteString(`\\`)
		case '\n':
			out.WriteString(`\n`)
		case '\r':
			out.WriteString(`\r`)
		case '\t':
			out.WriteString(`\t`)
		default:
			if r == rune(quote[0]) {
				out.WriteString(`\`)
			}
			if r < 0x20 || r == 0x7f {
				out.WriteString(`\x`)
				out.WriteString(strconv.FormatInt(int64(r), 16))
				continue
			}
			out.WriteRune(r)
		}
	}
	out.WriteString(quote)
	return out.String()
}

// isPySpace is the whitespace of Python's str.split/strip and of the \s of a
// str regular expression pattern.
func isPySpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// pyStrip is str.strip(): the string without its leading and trailing
// whitespace.
func pyStrip(s string) string {
	return strings.TrimFunc(s, isPySpace)
}

// isASCII reports whether a string is ASCII only.
func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// hexColorValue is HexColor(val) for a string argument, and HexColor(arg) of
// the integer values toColor falls back to.
func hexColorValue(s string) (color, toColorResult) {
	base := 10
	text := s
	if strings.HasPrefix(text, "#") {
		text = text[1:]
		base = 16
	} else if len(text) >= 2 && strings.EqualFold(text[:2], "0x") {
		text = text[2:]
		base = 16
	}
	n, status := pyInt(text, base)
	if status != attrOK {
		return color{}, colorSoft
	}
	return hexColorInt(n), colorOK
}

// hexColorInt is HexColor of an integer: the three low bytes of the value.
func hexColorInt(n *big.Int) color {
	value := new(big.Int).Mod(n, big.NewInt(1<<24))
	if value.Sign() < 0 {
		value.Add(value, big.NewInt(1<<24))
	}
	v := value.Uint64()
	return color{r: float64(v>>16&0xFF) / 255, g: float64(v>>8&0xFF) / 255, b: float64(v&0xFF) / 255}
}

// pyBytesDecode is asNative(bytes): the bytes as the UTF-8 text they hold.
func pyBytesDecode(data string) (string, bool) {
	if !utf8.ValidString(data) {
		return "", false
	}
	return data, true
}

// smallInt64 is a Python int value of a machine integer.
func smallInt64(n int64) color { return intColor(big.NewInt(n)) }
