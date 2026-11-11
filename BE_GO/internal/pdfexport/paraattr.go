package pdfexport

import (
	"math"
	"strconv"
	"strings"
)

// The attribute values of paraparser.py. Every conversion answers one of
// three things: a value, an exception reportlab raises (the export fails:
// ErrLayout) or an input whose Python semantics this package does not
// reproduce (ErrNotPorted: non-ASCII digits, nan and infinity, colour
// expressions beyond names, hex and css functions).

type attrStatus int

const (
	attrOK attrStatus = iota
	attrInvalid
	attrUnsupported
)

func pySpaceByte(b byte) bool {
	switch b {
	case ' ', '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f:
		return true
	}
	return false
}

func pyTrimASCII(s string) string {
	start, end := 0, len(s)
	for start < end && pySpaceByte(s[start]) {
		start++
	}
	for end > start && pySpaceByte(s[end-1]) {
		end--
	}
	return s[start:end]
}

func asciiOnly(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// digitRun reads digits of a base with single underscores between them from
// i: the digits without underscores and the end, or ok false for a malformed
// run.
func digitRun(s string, i, base int) (string, int, bool) {
	var out []byte
	for i < len(s) {
		c := s[i]
		isDigit := c >= '0' && c <= '9' || base == 16 && (c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F')
		switch {
		case isDigit:
			out = append(out, c)
			i++
		case c == '_':
			if len(out) == 0 || i+1 >= len(s) {
				return "", i, false
			}
			n := s[i+1]
			if !(n >= '0' && n <= '9' || base == 16 && (n >= 'a' && n <= 'f' || n >= 'A' && n <= 'F')) {
				return "", i, false
			}
			i++
		default:
			return string(out), i, true
		}
	}
	return string(out), i, true
}

// pyInt is int(s, base) for the values the markup meets.
func pyInt(s string, base int) (int64, attrStatus) {
	if !asciiOnly(s) {
		return 0, attrUnsupported
	}
	s = pyTrimASCII(s)
	neg := false
	if s != "" && (s[0] == '+' || s[0] == '-') {
		neg = s[0] == '-'
		s = s[1:]
	}
	if base == 16 && len(s) >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X') {
		return 0, attrUnsupported
	}
	digits, end, ok := digitRun(s, 0, base)
	if !ok || digits == "" || end != len(s) {
		return 0, attrInvalid
	}
	digits = strings.TrimLeft(digits, "0")
	if len(digits) > 15 {
		return 0, attrUnsupported
	}
	if digits == "" {
		return 0, attrOK
	}
	n, err := strconv.ParseInt(digits, base, 64)
	if err != nil {
		return 0, attrUnsupported
	}
	if neg {
		n = -n
	}
	return n, attrOK
}

// pyFloat is float(s).
func pyFloat(s string) (float64, attrStatus) {
	if !asciiOnly(s) {
		return 0, attrUnsupported
	}
	s = pyTrimASCII(s)
	body := s
	if body != "" && (body[0] == '+' || body[0] == '-') {
		body = body[1:]
	}
	switch strings.ToLower(body) {
	case "inf", "infinity", "nan":
		return 0, attrUnsupported
	}
	intPart, i, ok := digitRun(body, 0, 10)
	if !ok {
		return 0, attrInvalid
	}
	fracPart := ""
	if i < len(body) && body[i] == '.' {
		fracPart, i, ok = digitRun(body, i+1, 10)
		if !ok {
			return 0, attrInvalid
		}
	}
	if intPart == "" && fracPart == "" {
		return 0, attrInvalid
	}
	exponent := ""
	if i < len(body) && (body[i] == 'e' || body[i] == 'E') {
		j := i + 1
		sign := ""
		if j < len(body) && (body[j] == '+' || body[j] == '-') {
			sign = string(body[j])
			j++
		}
		var digits string
		digits, i, ok = digitRun(body, j, 10)
		if !ok || digits == "" {
			return 0, attrInvalid
		}
		exponent = "e" + sign + digits
	}
	if i != len(body) {
		return 0, attrInvalid
	}
	if intPart == "" {
		intPart = "0"
	}
	if fracPart == "" {
		fracPart = "0"
	}
	text := intPart + "." + fracPart + exponent
	if s != "" && s[0] == '-' {
		text = "-" + text
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsInf(value, 0) {
		return 0, attrUnsupported
	}
	return value, attrOK
}

// numVal is a number of _num: a value in points, or a relative one.
type numVal struct {
	v   float64
	rel bool
}

// paraConvNum is _convnum(s, unit, allowRelative).
func paraConvNum(s string, unit float64, allowRelative bool) (numVal, attrStatus) {
	if s == "" {
		return numVal{}, attrInvalid // IndexError
	}
	if (s[0] == '+' || s[0] == '-') && allowRelative {
		if n, st := pyInt(s, 10); st != attrInvalid {
			return numVal{float64(n) * unit, true}, st
		}
		f, st := pyFloat(s)
		return numVal{f * unit, true}, st
	}
	if n, st := pyInt(s, 10); st != attrInvalid {
		return numVal{float64(n) * unit, false}, st
	}
	f, st := pyFloat(s)
	return numVal{f * unit, false}, st
}

// The units of reportlab.lib.units, computed with float64 operations (not
// exact constants).
var (
	unitInch = 72.0
	unitCM   = divide(unitInch, 2.54)
	unitMM   = unitCM * 0.1
	unitPica = 12.0
)

func divide(a, b float64) float64 { return a / b }

// paraNum is _num(s, allowRelative=...): a number with an optional unit.
func paraNum(s string, allowRelative bool) (numVal, attrStatus) {
	if !asciiOnly(s) {
		return numVal{}, attrUnsupported
	}
	body := strings.TrimLeft(s, " \t\n\v\f\r\x1c\x1d\x1e\x1f")
	body = strings.TrimRight(body, " \t\n\v\f\r\x1c\x1d\x1e\x1f")
	if !strings.Contains(body, "\n") {
		for _, unit := range []struct {
			suffix string
			value  float64
		}{{"i", unitInch}, {"in", unitInch}, {"cm", unitCM}, {"mm", unitMM}, {"pt", 1}, {"pica", unitPica}} {
			if strings.HasSuffix(body, unit.suffix) {
				return paraConvNum(body[:len(body)-len(unit.suffix)], unit.value, allowRelative)
			}
		}
	}
	return paraConvNum(s, 1, allowRelative)
}

// paraBool is _bool.
func paraBool(s string) (bool, attrStatus) {
	if !asciiOnly(s) {
		return false, attrUnsupported
	}
	switch strings.ToLower(s) {
	case "true", "1", "yes":
		return true, attrOK
	case "false", "0", "no":
		return false, attrOK
	}
	return false, attrInvalid
}

// --- colours -----------------------------------------------------------------------------------------------------

func isBareWord(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_') {
			return false
		}
	}
	return true
}

// cssNumbers splits the arguments of a css colour function.
func cssFunction(s string) (name string, alpha bool, args []string, ok bool) {
	body := strings.TrimLeft(s, " \t\n\v\f\r\x1c\x1d\x1e\x1f")
	for _, candidate := range []string{"pcmyk", "cmyk", "rgb", "hsl"} {
		if !strings.HasPrefix(body, candidate) {
			continue
		}
		rest := body[len(candidate):]
		if strings.HasPrefix(rest, "a") {
			alpha = true
			rest = rest[1:]
		}
		rest = strings.TrimLeft(rest, " \t\n\v\f\r\x1c\x1d\x1e\x1f")
		if !strings.HasPrefix(rest, "(") {
			return "", false, nil, false
		}
		rest = strings.TrimRight(rest, " \t\n\v\f\r\x1c\x1d\x1e\x1f")
		if !strings.HasSuffix(rest, ")") {
			return "", false, nil, false
		}
		inner := rest[1 : len(rest)-1]
		if strings.Contains(inner, ")") {
			return "", false, nil, false
		}
		return candidate, alpha, strings.Split(inner, ","), true
	}
	return "", false, nil, false
}

func clamp(v, low, high float64) float64 { return math.Min(high, math.Max(low, v)) }

func hueToRGB(m1, m2, h float64) float64 {
	if h < 0 {
		h++
	}
	if h > 1 {
		h--
	}
	if h*6 < 1 {
		return m1 + (m2-m1)*h*6
	}
	if h*2 < 1 {
		return m2
	}
	if h*3 < 2 {
		return m1 + (m2-m1)*(4-6*h)
	}
	return m1
}

// paraCSSColor is cssParse for rgb(...), rgba(...), hsl(...) and hsla(...);
// the cmyk forms and any colour with an alpha other than 1 are not ported.
func paraCSSColor(s string) (color, attrStatus, bool) {
	if !asciiOnly(s) {
		return color{}, attrOK, false
	}
	name, alpha, args, ok := cssFunction(s)
	if !ok {
		return color{}, attrOK, false
	}
	if name == "pcmyk" || name == "cmyk" {
		want := 4
		if alpha {
			want++
		}
		if len(args) != want {
			return color{}, attrInvalid, true
		}
		return color{}, attrUnsupported, true
	}
	want := 3
	if alpha {
		want = 4
	}
	if len(args) != want {
		return color{}, attrInvalid, true
	}
	if alpha {
		a, st := pyFloat(args[3])
		if st != attrOK {
			return color{}, st, true
		}
		if clamp(a, 0, 1) != 1 {
			return color{}, attrUnsupported, true
		}
		args = args[:3]
	}
	pct := func(v string) (float64, attrStatus) {
		v = pyTrimASCII(v)
		if v == "" {
			return 0, attrInvalid
		}
		f, st := pyFloat(v[:len(v)-1])
		return clamp(f, 0, 100) / 100, st
	}
	if name == "hsl" {
		h, st := pyFloat(args[0])
		if st != attrOK {
			return color{}, st, true
		}
		hue := math.Mod(math.Mod(h, 360)+360, 360) / 360
		sat, st := pct(args[1])
		if st != attrOK {
			return color{}, st, true
		}
		light, st := pct(args[2])
		if st != attrOK {
			return color{}, st, true
		}
		var m2 float64
		if light <= 0.5 {
			m2 = light * (sat + 1)
		} else {
			m2 = light + sat - light*sat
		}
		m1 := light*2 - m2
		return color{r: hueToRGB(m1, m2, hue+1./3), g: hueToRGB(m1, m2, hue), b: hueToRGB(m1, m2, hue-1./3)}, attrOK, true
	}
	var out [3]float64
	percent := strings.Contains(args[0], "%")
	for i, arg := range args {
		if percent {
			v, st := pct(arg)
			if st != attrOK {
				return color{}, st, true
			}
			out[i] = float64(int(v*255+0.5)) / 255
			continue
		}
		f, st := pyFloat(arg)
		if st != attrOK {
			return color{}, st, true
		}
		if 0 < f && f <= 1 {
			f *= 255
		}
		out[i] = float64(int(clamp(f, 0, 255))) / 255
	}
	return color{r: out[0], g: out[1], b: out[2]}, attrOK, true
}

// paraColor is toColor(s) for a string: the css functions, the colour names
// (lower case), and the hex or decimal integer HexColor reads. A bare word
// that is none of these raises in reportlab; any other expression (tuples,
// Blacker(...), spaces around a name) is not ported.
func paraColor(s string) (color, attrStatus) {
	if !asciiOnly(s) {
		return color{}, attrUnsupported
	}
	if c, st, matched := paraCSSColor(s); matched {
		return c, st
	}
	lower := strings.ToLower(s)
	if c, ok := paraNamedColors[lower]; ok {
		return c, attrOK
	}
	if paraUnportedColors[lower] {
		return color{}, attrUnsupported
	}
	hexValue := func(digits string, base int) (color, attrStatus) {
		n, st := pyInt(digits, base)
		if st != attrOK {
			return color{}, st
		}
		return color{r: float64(n>>16&0xFF) / 255, g: float64(n>>8&0xFF) / 255, b: float64(n&0xFF) / 255}, attrOK
	}
	switch {
	case strings.HasPrefix(s, "#"):
		if isBareWord(s[1:]) {
			return hexValue(s[1:], 16)
		}
	case len(s) > 2 && strings.EqualFold(s[:2], "0x"):
		if isBareWord(s[2:]) {
			return hexValue(s[2:], 16)
		}
	case s != "" && strings.Trim(s, "0123456789") == "":
		return hexValue(s, 10)
	}
	if s == "" || isBareWord(s) && !(s[0] >= '0' && s[0] <= '9') {
		return color{}, attrInvalid
	}
	return color{}, attrUnsupported
}
