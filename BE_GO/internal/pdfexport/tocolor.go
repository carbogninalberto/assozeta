package pdfexport

import (
	"math"
	"strings"
	"unicode"
	"unicode/utf8"
)

// toColor (reportlab/lib/colors.py) of a string and of the Python values a
// colour attribute expression leaves: the css colour functions, the colour
// names, Blacker(...)/Whiter(...), ast.literal_eval, rl_extended_literal_eval
// and finally HexColor. Every failure reportlab raises is either one toColor
// swallows (a ValueError or SyntaxError: "soft") or one that fails the
// paragraph ("hard"); the caller only needs the difference inside a nested
// toColor.

// paraColor is toColor(s): a colour, or attrInvalid when reportlab raises.
func paraColor(s string) (color, attrStatus) {
	out, res := toColorString(s, nil)
	if res != colorOK {
		return color{}, attrInvalid
	}
	// paraparser's _applyAttributes reads v[0] of a list or tuple value: an
	// empty one raises IndexError.
	if (out.kind == colorTuple || out.kind == colorList) && len(out.items) == 0 {
		return color{}, attrInvalid
	}
	return out, attrOK
}

// toColorString is toColor(s, default) for a string: cssParse, the named
// colours, Blacker/Whiter, the two literal readers and HexColor.
func toColorString(s string, def *color) (color, toColorResult) {
	if c, status, matched := paraCSSColor(s); matched {
		if status != attrOK {
			return color{}, colorSoft
		}
		return c, colorOK
	}
	if c, ok := paraNamedColors[strings.ToLower(s)]; ok {
		return c, colorOK
	}
	if match, ok := blackerWhiterMatch(s); ok {
		f, status := pyFloat(match.number)
		if status != attrOK {
			return color{}, colorSoft
		}
		root, res := toColorString(match.root, nil)
		if res != colorOK {
			return color{}, res
		}
		return callBlackerWhiter(match.blacker, root, floatColor(f))
	}
	// ast.literal_eval parses the string without its leading spaces and tabs;
	// rl_extended_literal_eval parses it as it is (and refuses an indented
	// expression).
	if node, ok := parsePyExpression(strings.TrimLeft(s, " \t")); ok {
		value, res := literalEval(node)
		switch res {
		case evalOK:
			out, converted := toColorValue(value, nil)
			if converted == colorOK {
				return out, colorOK
			}
			if converted == colorHard {
				return color{}, colorHard
			}
		case evalHard:
			return color{}, colorHard
		}
	}
	if node, ok := parsePyExpression(s); ok {
		value, res := extendedEval(node)
		switch res {
		case evalOK:
			return value, colorOK // returned as it is, not through toColor
		case evalHard:
			return color{}, colorHard
		}
	}
	out, res := hexColorValue(s)
	if res == colorOK {
		return out, colorOK
	}
	if def != nil {
		return *def, colorOK
	}
	return color{}, colorSoft
}

// toColorValue is toColor of a Python value: a colour as it is, a tuple or
// list of 3 or 4 components, a string (read again), or HexColor.
func toColorValue(v color, def *color) (color, toColorResult) {
	switch v.kind {
	case colorRGB, colorCMYK:
		return v, colorOK
	case colorTuple, colorList:
		if len(v.items) < 3 || len(v.items) > 4 {
			return color{}, colorHard // AssertionError of the length
		}
		values := make([]float64, len(v.items))
		for i, item := range v.items {
			if !isIntOrFloat(item) {
				return color{}, colorHard // TypeError of min/max over mixed types
			}
			value, ok := numberFloat(item)
			if !ok {
				return color{}, colorHard
			}
			values[i] = value
		}
		if !(0 <= pyMinSeq(values) && pyMaxSeq(values) <= 1) {
			return color{}, colorHard // AssertionError of the range
		}
		if len(values) == 3 {
			return rgbColor(values[0], values[1], values[2]), colorOK
		}
		return newCMYKColor(values[0], values[1], values[2], values[3], 1, nil, 1, cmykPlain), colorOK
	case colorStr:
		return toColorString(v.text, def)
	case colorBytes:
		text, ok := pyBytesDecode(v.text)
		if !ok {
			return color{}, colorHard
		}
		return toColorString(text, def)
	case colorInt:
		return hexColorInt(v.num), colorOK
	case colorBool:
		n, ok := intArg(v)
		if !ok {
			return color{}, colorSoft
		}
		return hexColorInt(n), colorOK
	}
	// HexColor raises for every other value, and toColor turns that into
	// ValueError('Invalid color value %r') or answers its default.
	if def != nil {
		return *def, colorOK
	}
	return color{}, colorSoft
}

// --- css colours ----------------------------------------------------------------------------------------------------

// cssMatch is _re_css.match(s): the function name, whether it takes an alpha
// component, and the untrimmed components.
func cssMatch(s string) (name string, alpha bool, args []string, ok bool) {
	i := 0
	skipSpace := func() {
		for i < len(s) {
			r, size := utf8.DecodeRuneInString(s[i:])
			if !isPySpace(r) {
				break
			}
			i += size
		}
	}
	skipSpace()
	found := false
	for _, candidate := range []string{"pcmyk", "cmyk", "rgb", "hsl"} {
		if strings.HasPrefix(s[i:], candidate) {
			name, i, found = candidate, i+len(candidate), true
			break
		}
	}
	if !found {
		return "", false, nil, false
	}
	if i < len(s) && s[i] == 'a' {
		alpha = true
		i++
	}
	skipSpace()
	if i >= len(s) || s[i] != '(' {
		return "", false, nil, false
	}
	i++
	skipSpace()
	rest := s[i:]
	end := strings.IndexByte(rest, ')')
	if end < 0 {
		return "", false, nil, false
	}
	for _, r := range rest[end+1:] {
		if !isPySpace(r) {
			return "", false, nil, false
		}
	}
	return name, alpha, strings.Split(rest[:end], ","), true
}

// cssFloat is float(v) of a css component: pyFloat reads the infinities and
// NaNs and the literals beyond the float range as CPython does.
func cssFloat(v string) (float64, attrStatus) { return pyFloat(v) }

// cssPercent is cssParse.pcVal: float(v[:-1]) forced into [0, 100] and
// divided by 100.
func cssPercent(v string) (float64, attrStatus) {
	v = pyStrip(v)
	runes := []rune(v)
	if len(runes) == 0 {
		return 0, attrInvalid
	}
	value, status := cssFloat(string(runes[:len(runes)-1]))
	if status != attrOK {
		return 0, status
	}
	return pyMin(100, pyMax(0, value)) / 100, attrOK
}

// cssRGBPercent is cssParse.rgbPcVal: int(pcVal(v)*255+0.5)/255.
func cssRGBPercent(v string) (float64, attrStatus) {
	value, status := cssPercent(v)
	if status != attrOK {
		return 0, status
	}
	return math.Trunc(value*255+0.5) / 255, attrOK
}

// cssRGBValue is cssParse.rgbVal: a component in [0, 255], where a value in
// (0, 1] is a fraction of full intensity.
func cssRGBValue(v string) (float64, attrStatus) {
	value, status := cssFloat(pyStrip(v))
	if status != attrOK {
		return 0, status
	}
	if 0 < value && value <= 1 {
		value *= 255
	}
	return math.Trunc(pyMin(255, pyMax(0, value))) / 255, attrOK
}

// cssAlphaValue is cssParse.alphaVal(v, limit).
func cssAlphaValue(v string, limit float64) (float64, attrStatus) {
	value, status := cssFloat(v)
	if status != attrOK {
		return 0, status
	}
	return pyMin(limit, pyMax(0, value)), attrOK
}

// cssHueValue is cssParse.hueVal: the hue as a fraction of the circle.
func cssHueValue(v string) (float64, attrStatus) {
	value, status := cssFloat(pyStrip(v))
	if status != attrOK {
		return 0, status
	}
	return math.Mod(math.Mod(value, 360)+360, 360) / 360, attrOK
}

// paraCSSColor is cssParse(s): the css colour functions rgb, rgba, hsl, hsla,
// cmyk, cmyka, pcmyk and pcmyka. matched is false for a string of another
// shape, which toColor reads otherwise.
func paraCSSColor(s string) (color, attrStatus, bool) {
	name, hasAlpha, args, ok := cssMatch(s)
	if !ok {
		return color{}, attrOK, false
	}
	limit, components := 1.0, 3
	cmykForm := false
	switch name {
	case "cmyk":
		cmykForm, components = true, 4
	case "pcmyk":
		cmykForm, components, limit = true, 4, 100
	}
	want := components
	if hasAlpha {
		want++
	}
	if len(args) != want {
		return color{}, attrInvalid, true
	}
	alpha := limit
	if hasAlpha {
		value, status := cssAlphaValue(args[components], limit)
		if status != attrOK {
			return color{}, status, true
		}
		alpha = value
	}
	if cmykForm {
		var values [4]float64
		for i := range values {
			value, status := cssAlphaValue(args[i], limit)
			if status != attrOK {
				return color{}, status, true
			}
			values[i] = value
		}
		if limit > 1 {
			return newCMYKColor(values[0]/100, values[1]/100, values[2]/100, values[3]/100, 1, nil, alpha/100, cmykP),
				attrOK, true
		}
		return newCMYKColor(values[0], values[1], values[2], values[3], 1, nil, alpha, cmykPlain), attrOK, true
	}
	if name == "hsl" {
		hue, status := cssHueValue(args[0])
		if status != attrOK {
			return color{}, status, true
		}
		saturation, status := cssPercent(args[1])
		if status != attrOK {
			return color{}, status, true
		}
		light, status := cssPercent(args[2])
		if status != attrOK {
			return color{}, status, true
		}
		var m2 float64
		if light <= 0.5 {
			m2 = light * (saturation + 1)
		} else {
			m2 = light + saturation - light*saturation
		}
		m1 := light*2 - m2
		return rgbColorA(hue2rgbValue(m1, m2, hue+1./3), hue2rgbValue(m1, m2, hue), hue2rgbValue(m1, m2, hue-1./3), alpha),
			attrOK, true
	}
	percent := strings.Contains(args[0], "%")
	var values [3]float64
	for i := range values {
		var value float64
		var status attrStatus
		if percent {
			value, status = cssRGBPercent(args[i])
		} else {
			value, status = cssRGBValue(args[i])
		}
		if status != attrOK {
			return color{}, status, true
		}
		values[i] = value
	}
	return rgbColorA(values[0], values[1], values[2], alpha), attrOK, true
}

// --- Blacker/Whiter --------------------------------------------------------------------------------------

type blackerWhiter struct {
	blacker bool
	root    string
	number  string
}

// blackerWhiterMatch is (Blacker|Whiter)\((\w+)\,\s?([0-9.]+)\) with re.match:
// a prefix match, a Unicode word for the colour name, one optional whitespace
// and an ASCII decimal number.
func blackerWhiterMatch(s string) (blackerWhiter, bool) {
	var out blackerWhiter
	switch {
	case strings.HasPrefix(s, "Blacker"):
		out.blacker = true
		s = s[len("Blacker"):]
	case strings.HasPrefix(s, "Whiter"):
		s = s[len("Whiter"):]
	default:
		return out, false
	}
	if len(s) == 0 || s[0] != '(' {
		return out, false
	}
	s = s[1:]
	length := 0
	for length < len(s) {
		r, size := utf8.DecodeRuneInString(s[length:])
		if !isPyWordRune(r) {
			break
		}
		length += size
	}
	if length == 0 {
		return out, false
	}
	out.root = s[:length]
	s = s[length:]
	if len(s) == 0 || s[0] != ',' {
		return out, false
	}
	s = s[1:]
	if len(s) > 0 {
		if r, size := utf8.DecodeRuneInString(s); isPySpace(r) {
			s = s[size:]
		}
	}
	length = 0
	for length < len(s) && (s[length] >= '0' && s[length] <= '9' || s[length] == '.') {
		length++
	}
	if length == 0 {
		return out, false
	}
	out.number = s[:length]
	if length >= len(s) || s[length] != ')' {
		return out, false
	}
	return out, true
}

// isPyWordRune is the \w of a str regular expression: an alphanumeric
// character or an underscore.
func isPyWordRune(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r)
}
