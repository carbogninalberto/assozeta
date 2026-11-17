package pdfexport

import (
	"math"
	"math/big"
	"strconv"
	"strings"
)

// The colour functions rl_extended_literal_eval offers an expression
// (reportlab/lib/colors.py): the reportlab classes and the functions that make
// or read a colour. Each one reproduces the checks its Python body makes; a
// ValueError is a "soft" failure the extended reader swallows, any other
// exception one that fails the paragraph.

var (
	whiteColor       = rgbColor(1, 1, 1)
	blackColor       = rgbColor(0, 0, 0)
	transparentColor = rgbColorA(0, 0, 0, 0)
	cmykWhiteColor   = newCMYKColor(0, 0, 0, 0, 1, nil, 1, cmykPlain)
	pcmykWhiteColor  = newCMYKColor(0, 0, 0, 0, 1, nil, 1, cmykP)
	cmykBlackColor   = newCMYKColor(0, 0, 0, 1, 1, nil, 1, cmykPlain)
	pcmykBlackColor  = newCMYKColor(0, 0, 0, 1, 1, nil, 1, cmykP)
)

// paraSafeCallables are the names rl_extended_literal_eval may call: the
// callables of reportlab.lib.colors whose name toColor passes it.
var paraSafeCallables = map[string]bool{
	"Blacker": true, "CMYKColor": true, "CMYKColorSep": true, "Color": true, "ColorType": true,
	"HexColor": true, "PCMYKColor": true, "PCMYKColorSep": true, "Whiter": true,
	"_chooseEnforceColorSpace": true, "_enforceCMYK": true, "_enforceError": true, "_enforceRGB": true,
	"_enforceSEP": true, "_enforceSEP_BLACK": true, "_enforceSEP_CMYK": true, "asNative": true,
	"cmyk2rgb": true, "cmykDistance": true, "color2bw": true, "colorDistance": true, "cssParse": true,
	"describe": true, "fade": true, "fp_str": true, "getAllNamedColors": true, "hsl2rgb": true,
	"hue2rgb": true, "linearlyInterpolatedColor": true, "obj_R_G_B": true, "opaqueColor": true,
	"rgb2cmyk": true, "setColors": true, "toColor": true, "toColorOrNone": true,
}

func paraIsCallable(name string) bool { return paraSafeCallables[name] }

type paraParam struct {
	name   string
	def    color
	hasDef bool
}

func requiredParam(name string) paraParam { return paraParam{name: name} }

func defaultParam(name string, def color) paraParam {
	return paraParam{name: name, def: def, hasDef: true}
}

// noneValue is Python None.
var noneValue = color{kind: colorNone}

// bindParams is Python's binding of positional and keyword arguments to a
// signature: too many positional arguments, an unknown keyword, a keyword that
// names a parameter already given and a missing required parameter all fail.
func bindParams(args []color, kw map[string]color, params []paraParam) ([]color, bool) {
	if len(args) > len(params) {
		return nil, false
	}
	out := make([]color, len(params))
	provided := make([]bool, len(params))
	for i := range args {
		out[i] = args[i]
		provided[i] = true
	}
	for name, value := range kw {
		index := -1
		for i := range params {
			if params[i].name == name {
				index = i
				break
			}
		}
		if index < 0 || provided[index] {
			return nil, false
		}
		out[index] = value
		provided[index] = true
	}
	for i := range params {
		if provided[i] {
			continue
		}
		if !params[i].hasDef {
			return nil, false
		}
		out[i] = params[i].def
	}
	return out, true
}

// numberArg is a real number argument as a float; anything else is a TypeError
// of the arithmetic the Python body does.
func numberArg(c color) (float64, bool) {
	switch c.kind {
	case colorInt:
		if c.num == nil {
			return 0, true
		}
		f, _ := new(big.Float).SetInt(c.num).Float64()
		return f, true
	case colorBool, colorFloat:
		return c.f, true
	}
	return 0, false
}

// intArg is an integer argument, keeping big values exact for HexColor.
func intArg(c color) (*big.Int, bool) {
	switch c.kind {
	case colorInt:
		return new(big.Int).Set(c.num), true
	case colorBool:
		if c.f != 0 {
			return big.NewInt(1), true
		}
		return big.NewInt(0), true
	}
	return nil, false
}

// spotValue is a spotName argument as the colour value reportlab keeps: None
// is nil, any other value is kept as it is.
func spotValue(c color) *color {
	if c.kind == colorNone {
		return nil
	}
	out := c
	return &out
}

// opaqueColorValue is a colour object whose components are not numbers: it
// takes part in comparisons but no drawing (reportlab fails on it).
func opaqueColorValue(kind string, parts ...color) color {
	var out strings.Builder
	out.WriteString(kind)
	out.WriteByte(':')
	for _, part := range parts {
		appendColorKey(&out, part)
		out.WriteByte(';')
	}
	return color{kind: colorOpaque, key: out.String()}
}

// makeColor is Color.red, green, blue, alpha.
func makeColor(r, g, b, alpha color) (color, evalResult) {
	rf, okR := numberArg(r)
	gf, okG := numberArg(g)
	bf, okB := numberArg(b)
	af, okA := numberArg(alpha)
	if !okR || !okG || !okB || !okA {
		return opaqueColorValue("Color", r, g, b, alpha), evalOK
	}
	out := rgbColorA(rf, gf, bf, af)
	return out, evalOK
}

// makeCMYK is the body of CMYKColor.__init__, from its own arguments.
func makeCMYK(cyan, magenta, yellow, black, density, alpha color, spot color, class cmykClass,
	hasKnockout bool, knockout color, scale float64) (color, evalResult) {
	c, okC := numberArg(cyan)
	m, okM := numberArg(magenta)
	y, okY := numberArg(yellow)
	k, okK := numberArg(black)
	d, okD := numberArg(density)
	if !okC || !okM || !okY || !okK || !okD {
		return color{}, evalHard
	}
	a, okA := numberArg(alpha)
	if !okA {
		// The constructor keeps an alpha of another type; only drawing rejects it.
		return opaqueColorValue("CMYKColor", cyan, magenta, yellow, black, density, alpha, spot), evalOK
	}
	if scale != 1 {
		c, m, y, k, d, a = c/scale, m/scale, y/scale, k/scale, d/scale, a/scale
	}
	_ = hasKnockout
	_ = knockout
	return newCMYKColor(c, m, y, k, d, spotValue(spot), a, class), evalOK
}

// pscale is the scale PCMYKColor divides its arguments by.
const pscale = 100

// callParaCallable calls one of paraSafeCallables.
func callParaCallable(name string, args []color, kwargs map[string]color) (color, evalResult) {
	switch name {
	case "Color", "ColorType":
		values, ok := bindParams(args, kwargs, []paraParam{
			defaultParam("red", floatColor(0)), defaultParam("green", floatColor(0)),
			defaultParam("blue", floatColor(0)), defaultParam("alpha", floatColor(1))})
		if !ok {
			return color{}, evalHard
		}
		return makeColor(values[0], values[1], values[2], values[3])
	case "CMYKColor":
		values, ok := bindParams(args, kwargs, []paraParam{
			defaultParam("cyan", floatColor(0)), defaultParam("magenta", floatColor(0)),
			defaultParam("yellow", floatColor(0)), defaultParam("black", floatColor(0)),
			defaultParam("spotName", noneValue), defaultParam("density", floatColor(1)),
			defaultParam("knockout", noneValue), defaultParam("alpha", floatColor(1))})
		if !ok {
			return color{}, evalHard
		}
		return makeCMYK(values[0], values[1], values[2], values[3], values[5], values[7], values[4],
			cmykPlain, true, values[6], 1)
	case "PCMYKColor":
		values, ok := bindParams(args, kwargs, []paraParam{
			requiredParam("cyan"), requiredParam("magenta"), requiredParam("yellow"), requiredParam("black"),
			defaultParam("density", floatColor(pscale)), defaultParam("spotName", noneValue),
			defaultParam("knockout", noneValue), defaultParam("alpha", floatColor(pscale))})
		if !ok {
			return color{}, evalHard
		}
		return makeCMYK(values[0], values[1], values[2], values[3], values[4], values[7], values[5],
			cmykP, true, values[6], pscale)
	case "CMYKColorSep":
		values, ok := bindParams(args, kwargs, []paraParam{
			defaultParam("cyan", floatColor(0)), defaultParam("magenta", floatColor(0)),
			defaultParam("yellow", floatColor(0)), defaultParam("black", floatColor(0)),
			defaultParam("spotName", noneValue), defaultParam("density", floatColor(1)),
			defaultParam("alpha", floatColor(1))})
		if !ok {
			return color{}, evalHard
		}
		return makeCMYK(values[0], values[1], values[2], values[3], values[5], values[6], values[4],
			cmykSep, false, noneValue, 1)
	case "PCMYKColorSep":
		values, ok := bindParams(args, kwargs, []paraParam{
			defaultParam("cyan", floatColor(0)), defaultParam("magenta", floatColor(0)),
			defaultParam("yellow", floatColor(0)), defaultParam("black", floatColor(0)),
			defaultParam("spotName", noneValue), defaultParam("density", floatColor(pscale)),
			defaultParam("alpha", floatColor(pscale))})
		if !ok {
			return color{}, evalHard
		}
		return makeCMYK(values[0], values[1], values[2], values[3], values[5], values[6], values[4],
			cmykPSep, false, noneValue, pscale)
	case "HexColor":
		values, ok := bindParams(args, kwargs, []paraParam{
			requiredParam("val"), defaultParam("htmlOnly", boolColor(false)), defaultParam("hasAlpha", boolColor(false))})
		if !ok {
			return color{}, evalHard
		}
		return callHexColor(values[0], values[1], values[2])
	case "Blacker", "Whiter":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("c"), requiredParam("f")})
		if !ok {
			return color{}, evalHard
		}
		return callBlackerWhiter(name == "Blacker", values[0], values[1])
	case "linearlyInterpolatedColor":
		values, ok := bindParams(args, kwargs, []paraParam{
			requiredParam("c0"), requiredParam("c1"), requiredParam("x0"), requiredParam("x1"), requiredParam("x")})
		if !ok {
			return color{}, evalHard
		}
		return linearlyInterpolatedColor(values[0], values[1], values[2], values[3], values[4])
	case "fade":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("aSpotColor"), requiredParam("percentages")})
		if !ok {
			return color{}, evalHard
		}
		return callFade(values[0], values[1])
	case "cmyk2rgb":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("cmyk"), defaultParam("density", floatColor(1))})
		if !ok {
			return color{}, evalHard
		}
		return callCmyk2rgb(values[0])
	case "rgb2cmyk":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("r"), requiredParam("g"), requiredParam("b")})
		if !ok {
			return color{}, evalHard
		}
		return callRgb2cmyk(values[0], values[1], values[2])
	case "hsl2rgb":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("h"), requiredParam("s"), requiredParam("l")})
		if !ok {
			return color{}, evalHard
		}
		return callHsl2rgb(values[0], values[1], values[2])
	case "hue2rgb":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("m1"), requiredParam("m2"), requiredParam("h")})
		if !ok {
			return color{}, evalHard
		}
		return callHue2rgb(values[0], values[1], values[2])
	case "color2bw":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("colorRGB")})
		if !ok {
			return color{}, evalHard
		}
		return callColor2bw(values[0])
	case "colorDistance", "cmykDistance":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("col1"), requiredParam("col2")})
		if !ok {
			return color{}, evalHard
		}
		return callColorDistance(name == "cmykDistance", values[0], values[1])
	case "obj_R_G_B":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("c")})
		if !ok {
			return color{}, evalHard
		}
		return callObjRGB(values[0])
	case "opaqueColor":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("c")})
		if !ok {
			return color{}, evalHard
		}
		return callOpaqueColor(values[0])
	case "cssParse":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("s")})
		if !ok {
			return color{}, evalHard
		}
		return callCSSParse(values[0])
	case "describe":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("aColor"), defaultParam("mode", floatColor(0))})
		if !ok {
			return color{}, evalHard
		}
		return callDescribe(values[0], values[1])
	case "fp_str":
		if len(kwargs) > 0 {
			return color{}, evalHard
		}
		return callFPStr(args)
	case "getAllNamedColors":
		if len(args) > 0 || len(kwargs) > 0 {
			return color{}, evalHard
		}
		return colorObjectKey("dict", "getAllNamedColors"), evalOK
	case "asNative":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("v"), defaultParam("enc", strColor("utf8"))})
		if !ok {
			return color{}, evalHard
		}
		return callAsNative(values[0], values[1])
	case "_enforceCMYK", "_enforceRGB", "_enforceSEP", "_enforceSEP_BLACK", "_enforceSEP_CMYK":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("c")})
		if !ok {
			return color{}, evalHard
		}
		return callEnforce(name, values[0])
	case "_enforceError":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("kind"), requiredParam("c"), requiredParam("tc")})
		if !ok {
			return color{}, evalHard
		}
		_ = values
		return color{}, colorSoft
	case "_chooseEnforceColorSpace":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("enforceColorSpace")})
		if !ok {
			return color{}, evalHard
		}
		return callChooseEnforce(values[0])
	case "toColor", "toColorOrNone":
		values, ok := bindParams(args, kwargs, []paraParam{requiredParam("arg"), defaultParam("default", noneValue)})
		if !ok {
			return color{}, evalHard
		}
		return callToColor(name == "toColorOrNone", values[0], values[1])
	case "setColors":
		if len(args) > 0 {
			return color{}, evalHard
		}
		if len(kwargs) == 0 {
			return noneValue, evalOK
		}
		return color{}, evalHard
	}
	return color{}, evalSoft
}

// callHexColor is HexColor(val, htmlOnly, hasAlpha).
func callHexColor(val, htmlOnly, hasAlpha color) (color, evalResult) {
	alpha := colorTruthy(hasAlpha)
	var n *big.Int
	switch val.kind {
	case colorStr, colorBytes:
		text := val.text
		if val.kind == colorBytes {
			decoded, ok := pyBytesDecode(text)
			if !ok {
				return color{}, evalHard
			}
			text = decoded
		}
		base := 10
		if strings.HasPrefix(text, "#") {
			text = text[1:]
			base = 16
		} else {
			if colorTruthy(htmlOnly) {
				return color{}, colorSoft
			}
			if len(text) >= 2 && strings.EqualFold(text[:2], "0x") {
				text = text[2:]
				base = 16
			}
		}
		value, status := pyInt(text, base)
		if status != attrOK {
			return color{}, colorSoft
		}
		n = value
	default:
		value, ok := intArg(val)
		if !ok {
			return color{}, evalHard
		}
		n = value
	}
	return hexColorWithAlpha(n, alpha), evalOK
}

// hexColorWithAlpha is HexColor's Color of an integer value.
func hexColorWithAlpha(n *big.Int, alpha bool) color {
	modulus := big.NewInt(1 << 24)
	if alpha {
		modulus = big.NewInt(1 << 32)
	}
	value := new(big.Int).Mod(n, modulus)
	if value.Sign() < 0 {
		value.Add(value, modulus)
	}
	v := value.Uint64()
	byteAt := func(shift uint) float64 { return float64(v>>shift&0xFF) / 255 }
	if alpha {
		return rgbColorA(byteAt(24), byteAt(16), byteAt(8), byteAt(0))
	}
	return rgbColor(byteAt(16), byteAt(8), byteAt(0))
}

// callBlackerWhiter is Blacker(c, f) and Whiter(c, f).
func callBlackerWhiter(blacker bool, c, f color) (color, evalResult) {
	base, res := toColorValue(c, nil)
	if res != colorOK {
		return color{}, res
	}
	if base.kind == colorCMYK && (base.class == cmykSep || base.class == cmykPSep) {
		if base.class == cmykPSep {
			base.class = cmykP
		} else {
			base.class = cmykPlain
		}
	}
	var other color
	switch {
	case base.kind == colorCMYK && base.class == cmykP:
		if blacker {
			other = pcmykBlackColor
		} else {
			other = pcmykWhiteColor
		}
	case base.kind == colorCMYK:
		if blacker {
			other = cmykBlackColor
		} else {
			other = cmykWhiteColor
		}
	default:
		if blacker {
			other = blackColor
		} else {
			other = whiteColor
		}
	}
	if blacker {
		return linearlyInterpolatedColor(other, base, floatColor(0), floatColor(1), f)
	}
	return linearlyInterpolatedColor(other, base, floatColor(0), floatColor(1), f)
}

// linearlyInterpolatedColor is linearlyInterpolatedColor of reportlab's
// colors.py, including the PCMYKColor its "c1 is white" branch returns.
func linearlyInterpolatedColor(c0, c1, x0Arg, x1Arg, xArg color) (color, evalResult) {
	if colorClassName(c0) != colorClassName(c1) {
		return color{}, colorSoft
	}
	x0, ok0 := numberArg(x0Arg)
	x1, ok1 := numberArg(x1Arg)
	x, okx := numberArg(xArg)
	if !ok0 || !ok1 || !okx {
		return color{}, evalHard
	}
	if x1 < x0 {
		x0, x1 = x1, x0
		c0, c1 = c1, c0
	}
	if x < x0-1e-8 || x > x1+1e-8 {
		return color{}, colorSoft
	}
	if x <= x0 {
		return c0, evalOK
	}
	if x >= x1 {
		return c1, evalOK
	}
	dx := x1 - x0
	x = x - x0
	switch colorClassName(c0) {
	case "Color":
		r := c0.r + x*(c1.r-c0.r)/dx
		g := c0.g + x*(c1.g-c0.g)/dx
		b := c0.b + x*(c1.b-c0.b)/dx
		a := c0.alphaValue() + x*(c1.alphaValue()-c0.alphaValue())/dx
		return rgbColorA(r, g, b, a), evalOK
	case "CMYKColor":
		switch {
		case cmykDistanceValue(c0, c1) < 1e-8:
			if !spotEq(c0.spot, c1.spot) {
				return color{}, evalHard // AssertionError: different spotName
			}
			d := c0.dens + x*(c1.dens-c0.dens)/dx
			a := c0.alphaValue() + x*(c1.alphaValue()-c0.alphaValue())/dx
			return newCMYKColor(c0.c, c0.m, c0.y, c0.k, d, c0.spot, a, cmykPlain), evalOK
		case cmykDistanceValue(c0, cmykWhiteColor) < 1e-8:
			d := x * c1.dens / dx
			a := x * c1.alphaValue() / dx
			return newCMYKColor(c1.c, c1.m, c1.y, c1.k, d, c1.spot, a, cmykPlain), evalOK
		case cmykDistanceValue(c1, cmykWhiteColor) < 1e-8:
			d := c0.dens * (1 - x/dx)
			a := c0.alphaValue() * (1 - x/dx)
			return newCMYKColor(c0.c, c0.m, c0.y, c0.k, d, c0.spot, a, cmykP), evalOK
		default:
			c := c0.c + x*(c1.c-c0.c)/dx
			m := c0.m + x*(c1.m-c0.m)/dx
			y := c0.y + x*(c1.y-c0.y)/dx
			k := c0.k + x*(c1.k-c0.k)/dx
			d := c0.dens + x*(c1.dens-c0.dens)/dx
			a := c0.alphaValue() + x*(c1.alphaValue()-c0.alphaValue())/dx
			return newCMYKColor(c, m, y, k, d, nil, a, cmykPlain), evalOK
		}
	case "PCMYKColor":
		switch {
		case cmykDistanceValue(c0, c1) < 1e-8:
			if !spotEq(c0.spot, c1.spot) {
				return color{}, evalHard
			}
			d := c0.dens + x*(c1.dens-c0.dens)/dx
			a := c0.alphaValue() + x*(c1.alphaValue()-c0.alphaValue())/dx
			return newCMYKColor(c0.c, c0.m, c0.y, c0.k, d, c0.spot, a, cmykP), evalOK
		case cmykDistanceValue(c0, pcmykWhiteColor) < 1e-8:
			d := x * c1.dens / dx
			a := x * c1.alphaValue() / dx
			return newCMYKColor(c1.c, c1.m, c1.y, c1.k, d, c1.spot, a, cmykP), evalOK
		case cmykDistanceValue(c1, pcmykWhiteColor) < 1e-8:
			d := c0.dens * (1 - x/dx)
			a := c0.alphaValue() * (1 - x/dx)
			return newCMYKColor(c0.c, c0.m, c0.y, c0.k, d, c0.spot, a, cmykP), evalOK
		default:
			c := c0.c + x*(c1.c-c0.c)/dx
			m := c0.m + x*(c1.m-c0.m)/dx
			y := c0.y + x*(c1.y-c0.y)/dx
			k := c0.k + x*(c1.k-c0.k)/dx
			d := c0.dens + x*(c1.dens-c0.dens)/dx
			a := c0.alphaValue() + x*(c1.alphaValue()-c0.alphaValue())/dx
			return newCMYKColor(c, m, y, k, d, nil, a, cmykP), evalOK
		}
	}
	return color{}, colorSoft
}

// cmykDistanceValue is cmykDistance of two CMYK colours.
func cmykDistanceValue(a, b color) float64 {
	return math.Sqrt(square(a.c-b.c) + square(a.m-b.m) + square(a.y-b.y) + square(a.k-b.k))
}

func square(v float64) float64 { return v * v }

// callFade is fade(aSpotColor, percentages).
func callFade(spot, percentages color) (color, evalResult) {
	var percents []color
	switch percentages.kind {
	case colorTuple, colorList:
		percents = percentages.items
	case colorStr:
		for _, r := range percentages.text {
			percents = append(percents, strColor(string(r)))
		}
	case colorBytes:
		for i := 0; i < len(percentages.text); i++ {
			percents = append(percents, smallInt64(int64(percentages.text[i])))
		}
	case colorDict:
		for _, pair := range percentages.pairs {
			percents = append(percents, pair.key)
		}
	default:
		return color{}, evalHard // not iterable
	}
	if spot.kind != colorCMYK {
		return color{}, evalHard // no cyan/magenta attributes
	}
	out := color{kind: colorList}
	for _, percent := range percents {
		p, ok := numberArg(percent)
		if !ok {
			return color{}, evalHard
		}
		frac := p * 0.01
		out.items = append(out.items, newCMYKColor(frac*spot.c, frac*spot.m, frac*spot.y, frac*spot.k,
			frac*spot.dens, spot.spot, 1, cmykPlain))
	}
	return out, evalOK
}

// callCmyk2rgb is cmyk2rgb(cmyk, density=1).
func callCmyk2rgb(cmyk color) (color, evalResult) {
	var parts []color
	switch cmyk.kind {
	case colorTuple, colorList:
		parts = cmyk.items
	case colorStr:
		for _, r := range cmyk.text {
			parts = append(parts, strColor(string(r)))
		}
	case colorDict:
		for _, pair := range cmyk.pairs {
			parts = append(parts, pair.key)
		}
	default:
		return color{}, evalHard
	}
	if len(parts) != 4 {
		return color{}, colorSoft // ValueError: not enough values to unpack
	}
	values := make([]float64, 4)
	for i, part := range parts {
		value, ok := numberArg(part)
		if !ok {
			return color{}, evalHard
		}
		values[i] = value
	}
	r, g, b := cmyk2rgbTuple(values[0], values[1], values[2], values[3])
	return seqColor([]color{floatColor(r), floatColor(g), floatColor(b)}, false), evalOK
}

// callRgb2cmyk is rgb2cmyk(r, g, b).
func callRgb2cmyk(r, g, b color) (color, evalResult) {
	rf, ok := numberArg(r)
	if !ok {
		return color{}, evalHard
	}
	gf, ok := numberArg(g)
	if !ok {
		return color{}, evalHard
	}
	bf, ok := numberArg(b)
	if !ok {
		return color{}, evalHard
	}
	c := 1 - rf
	m := 1 - gf
	y := 1 - bf
	k := pyMin(c, pyMin(m, y))
	c = pyMin(1, pyMax(0, c-k))
	m = pyMin(1, pyMax(0, m-k))
	y = pyMin(1, pyMax(0, y-k))
	k = pyMin(1, pyMax(0, k))
	return seqColor([]color{floatColor(c), floatColor(m), floatColor(y), floatColor(k)}, false), evalOK
}

// hue2rgb is hue2rgb(m1, m2, h).
func hue2rgbValue(m1, m2, h float64) float64 {
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

// callHue2rgb is hue2rgb(m1, m2, h): the branches that only return one of
// their arguments take it as it is.
func callHue2rgb(m1, m2, h color) (color, evalResult) {
	hf, ok := numberArg(h)
	if !ok {
		return color{}, evalHard
	}
	if hf < 0 {
		hf++
	}
	if hf > 1 {
		hf--
	}
	if hf*6 < 1 {
		a, okA := numberArg(m1)
		b, okB := numberArg(m2)
		if !okA || !okB {
			return color{}, evalHard
		}
		return floatColor(a + (b-a)*hf*6), evalOK
	}
	if hf*2 < 1 {
		return m2, evalOK
	}
	if hf*3 < 2 {
		a, okA := numberArg(m1)
		b, okB := numberArg(m2)
		if !okA || !okB {
			return color{}, evalHard
		}
		return floatColor(a + (b-a)*(4-6*hf)), evalOK
	}
	return m1, evalOK
}

// callHsl2rgb is hsl2rgb(h, s, l).
func callHsl2rgb(h, s, l color) (color, evalResult) {
	hf, ok := numberArg(h)
	if !ok {
		return color{}, evalHard
	}
	sf, ok := numberArg(s)
	if !ok {
		return color{}, evalHard
	}
	lf, ok := numberArg(l)
	if !ok {
		return color{}, evalHard
	}
	var m2 float64
	if lf <= 0.5 {
		m2 = lf * (sf + 1)
	} else {
		m2 = lf + sf - lf*sf
	}
	m1 := lf*2 - m2
	return seqColor([]color{floatColor(hue2rgbValue(m1, m2, hf+1./3)), floatColor(hue2rgbValue(m1, m2, hf)),
		floatColor(hue2rgbValue(m1, m2, hf-1./3))}, false), evalOK
}

// callColor2bw is color2bw(colorRGB).
func callColor2bw(c color) (color, evalResult) {
	if c.kind != colorRGB && c.kind != colorCMYK {
		return color{}, evalHard // no red attribute
	}
	n := (c.r + c.g + c.b) / 3.0
	return rgbColorA(n, n, n, c.alphaValue()), evalOK
}

// callColorDistance is colorDistance(col1, col2) and cmykDistance.
func callColorDistance(cmyk bool, a, b color) (color, evalResult) {
	if cmyk {
		if a.kind != colorCMYK || b.kind != colorCMYK {
			return color{}, evalHard // AttributeError: no cyan
		}
		return floatColor(cmykDistanceValue(a, b)), evalOK
	}
	if !isColourValue(a) || !isColourValue(b) {
		return color{}, evalHard // AttributeError: no red
	}
	return floatColor(math.Sqrt(square(a.r-b.r) + square(a.g-b.g) + square(a.b-b.b))), evalOK
}

// callObjRGB is obj_R_G_B(c).
func callObjRGB(c color) (color, evalResult) {
	switch c.kind {
	case colorRGB, colorCMYK:
		return seqColor([]color{floatColor(c.r), floatColor(c.g), floatColor(c.b)}, false), evalOK
	case colorTuple, colorList:
		switch len(c.items) {
		case 3:
			return c, evalOK
		case 4:
			converted, res := toColorValue(c, nil)
			if res != colorOK {
				return color{}, res
			}
			return seqColor([]color{floatColor(converted.r), floatColor(converted.g), floatColor(converted.b)}, false), evalOK
		}
		return color{}, evalHard // the %r of a tuple raises TypeError
	}
	return noneValue, evalOK
}

// callOpaqueColor is opaqueColor(c).
func callOpaqueColor(c color) (color, evalResult) {
	if c.kind != colorRGB && c.kind != colorCMYK {
		return boolColor(false), evalOK
	}
	return boolColor(c.alphaValue() > 0), evalOK
}

// callCSSParse is cssParse(s): a colour, or None for a string of another shape.
func callCSSParse(s color) (color, evalResult) {
	if s.kind != colorStr {
		return color{}, evalHard // the pattern of a str does not match bytes
	}
	out, status, matched := paraCSSColor(s.text)
	if !matched {
		return noneValue, evalOK
	}
	if status != attrOK {
		return color{}, colorSoft
	}
	return out, evalOK
}

// callDescribe is describe(aColor, mode=0).
func callDescribe(c, mode color) (color, evalResult) {
	if c.kind != colorRGB && c.kind != colorCMYK {
		return color{}, evalHard // AttributeError: no red
	}
	modeF, ok := numberArg(mode)
	if !ok {
		return color{}, evalHard
	}
	if modeF > 1 && modeF != 2 {
		return color{}, colorSoft // ValueError: illegal mode
	}
	closest := math.Inf(1)
	name := ""
	for _, candidate := range paraNamedColorOrder {
		value, ok := paraColorNames[candidate]
		if !ok {
			continue
		}
		distance := math.Sqrt(square(c.r-value.r) + square(c.g-value.g) + square(c.b-value.b))
		if distance < closest {
			closest, name = distance, candidate
		}
	}
	if modeF <= 1 {
		text := "best match is " + name + ", distance " + fpFixed(closest, 4)
		if modeF == 0 {
			return noneValue, evalOK
		}
		return strColor(text), evalOK
	}
	return seqColor([]color{strColor(name), floatColor(closest)}, false), evalOK
}

// fpFixed is Python's '%.<digits>f' of a float.
func fpFixed(value float64, digits int) string {
	return strconv.FormatFloat(value, 'f', digits, 64)
}

// callFPStr is fp_str(*a): the numbers or one sequence of numbers joined by
// spaces in reportlab's number form.
func callFPStr(args []color) (color, evalResult) {
	values := args
	if len(args) == 1 && (args[0].kind == colorTuple || args[0].kind == colorList) {
		values = args[0].items
	}
	parts := make([]string, len(values))
	for i, value := range values {
		f, ok := numberArg(value)
		if !ok {
			return color{}, evalHard
		}
		parts[i] = fpStr(f)
	}
	return strColor(strings.Join(parts, " ")), evalOK
}

// callAsNative is asNative(v, enc='utf8').
func callAsNative(v, enc color) (color, evalResult) {
	if v.kind == colorStr {
		return v, evalOK
	}
	if v.kind != colorBytes {
		return color{}, evalHard // no decode method
	}
	if enc.kind != colorStr {
		return color{}, evalHard
	}
	switch strings.ToLower(enc.text) {
	case "utf8", "utf-8", "utf_8":
		text, ok := pyBytesDecode(v.text)
		if !ok {
			return color{}, evalHard
		}
		return strColor(text), evalOK
	case "latin1", "latin-1", "latin_1", "iso-8859-1", "iso8859-1":
		var out strings.Builder
		for i := 0; i < len(v.text); i++ {
			out.WriteRune(rune(v.text[i]))
		}
		return strColor(out.String()), evalOK
	case "ascii", "us-ascii":
		if !isASCII(v.text) {
			return color{}, evalHard
		}
		return strColor(v.text), evalOK
	}
	return color{}, evalHard
}

// callEnforce is _enforceCMYK, _enforceRGB, _enforceSEP, _enforceSEP_BLACK and
// _enforceSEP_CMYK.
func callEnforce(name string, c color) (color, evalResult) {
	value, res := toColorValue(c, nil)
	if res != colorOK {
		return color{}, res
	}
	switch name {
	case "_enforceSEP":
		if !isSepValue(value) {
			return color{}, colorSoft
		}
		return value, evalOK
	case "_enforceSEP_BLACK":
		if !isSepValue(value) {
			if isColourValue(value) && value.r == value.g && value.g == value.b {
				return newCMYKColor(0, 0, 0, 1, 1-value.r, nil, 1, cmykPlain), evalOK
			}
			if !(value.kind == colorCMYK && value.c == 0 && value.m == 0 && value.y == 0) {
				return color{}, colorSoft
			}
		}
		return value, evalOK
	case "_enforceSEP_CMYK":
		if !isSepValue(value) {
			if isColourValue(value) && value.r == value.g && value.g == value.b {
				return newCMYKColor(0, 0, 0, 1, 1-value.r, nil, 1, cmykPlain), evalOK
			}
			if value.kind != colorCMYK {
				return color{}, colorSoft
			}
		}
		return value, evalOK
	case "_enforceCMYK":
		if value.kind != colorCMYK {
			if isColourValue(value) && value.r == value.g && value.g == value.b {
				return newCMYKColor(0, 0, 0, 1-value.r, 1, nil, value.alphaValue(), cmykPlain), evalOK
			}
			return color{}, colorSoft
		}
		if isSepValue(value) {
			value.class = cmykPlain
		}
		return value, evalOK
	case "_enforceRGB":
		if value.kind == colorCMYK {
			if value.c == 0 && value.m == 0 && value.y == 0 {
				v := 1 - value.k*value.dens
				return rgbColorA(v, v, v, value.alphaValue()), evalOK
			}
			return color{}, colorSoft
		}
		return value, evalOK
	}
	return color{}, colorSoft
}

// isColourValue is reportlab's isinstance(value, Color): an RGB or a CMYK
// colour.
func isColourValue(c color) bool { return c.kind == colorRGB || c.kind == colorCMYK }

func isSepValue(c color) bool {
	return c.kind == colorCMYK && (c.class == cmykSep || c.class == cmykPSep)
}

// callChooseEnforce is _chooseEnforceColorSpace(enforceColorSpace).
func callChooseEnforce(v color) (color, evalResult) {
	if v.kind == colorNone {
		return noneValue, evalOK
	}
	if v.kind != colorStr {
		return color{}, colorSoft
	}
	switch strings.ToUpper(v.text) {
	case "CMYK":
		return colorObjectKey("func", "_enforceCMYK"), evalOK
	case "RGB":
		return colorObjectKey("func", "_enforceRGB"), evalOK
	case "SEP":
		return colorObjectKey("func", "_enforceSEP"), evalOK
	case "SEP_BLACK":
		return colorObjectKey("func", "_enforceSEP_BLACK"), evalOK
	case "SEP_CMYK":
		return colorObjectKey("func", "_enforceSEP_CMYK"), evalOK
	}
	return color{}, colorSoft
}

// callToColor is toColor(arg, default) and toColorOrNone(arg, default).
func callToColor(orNone bool, arg, def color) (color, evalResult) {
	if orNone && arg.kind == colorNone {
		return noneValue, evalOK
	}
	var defaultArg *color
	if def.kind != colorNone {
		defaultArg = &def
	}
	out, res := toColorValue(arg, defaultArg)
	if res != colorOK {
		return color{}, res
	}
	return out, evalOK
}
