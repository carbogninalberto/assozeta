// Package pilplaceholder reproduces, byte for byte, the PNG that Django's
// generate_image_with_text (BE/application/utils/api_utils.py) produces when
// its result is saved with image.save(buffer, format='PNG') under Pillow
// 13.0.0:
//
//	img = Image.new("RGB", (600, 100), "white")
//	font = ImageFont.load_default()          # truetype(DejaVuSans) raises OSError
//	for line in text.split("\n"):
//	    w = draw.textbbox((0, 0), line, font=font) width; text_width = max(...)
//	    draw.text((5, y), line, fill="black", font=font); y += 15
//	img = img.crop((0, 0, text_width + 30, y + 10))
//
// The DejaVu font file is not installed where Django runs, so the font is
// always Pillow's embedded Aileron Regular at its default size of 10 pixels,
// rendered by FreeType with Pillow's basic layout (no libraqm). With basic
// layout every glyph bitmap is independent of its position and is placed at an
// integer pixel offset, so the package embeds the glyph bitmaps, metrics and
// character map baked from that FreeType build (fontdata.bin.gz) and
// re-implements Pillow's layout, mask blending, drawing, cropping and PNG
// encoding around them. The font has no kerning table FreeType uses and every
// hinted advance is a whole number of pixels.
//
// Three things in the Python function are easy to miss and are reproduced:
// the crop box may exceed the 600x100 canvas, and Pillow fills the outside
// with black; text below row 100 or right of column 600 is clipped; and a
// character the font lacks (every accented letter, for example) is drawn as
// the font's .notdef box.
//
// The pixels are the same wherever Pillow 13.0.0 runs, but the PNG bytes are
// not: Pillow's wheels for macOS and Windows bundle zlib-ng 2.3.3, while the
// manylinux wheels link the system libz.so.1, which is classic zlib on Debian
// (1.2.13 in the production image). The two compress the same filtered lines
// into different streams, so the caller chooses which one to reproduce; see
// Zlib.
//
// fontdata.bin.gz and the golden data under testdata are written by
// TESTS/adapters/go/pil_placeholder_golden.py.
package pilplaceholder

import (
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrUnsupported is returned for inputs whose Django output Go cannot
// reproduce: text that is not valid UTF-8 (a Python str has no such value) and
// the sizes at which Pillow itself raises instead of returning an image.
var ErrUnsupported = refusal.New("pilplaceholder: input outside the supported subset")

const (
	canvasWidth  = 600
	canvasHeight = 100
	originX      = 5
	originY      = 5
	lineStep     = 15
	marginX      = 30
	marginY      = 10

	// ImageFont.MAX_STRING_LENGTH: a longer line raises ValueError.
	maxStringLength = 1_000_000
	// Image.MAX_IMAGE_PIXELS: more than twice this many pixels raises
	// DecompressionBombError, both for a text mask and for the crop.
	maxImagePixels = 89478485
)

// Zlib selects the deflate implementation behind Pillow's PNG encoder.
type Zlib int

const (
	// ZlibNG is zlib-ng 2.3.3 as bundled in Pillow's macOS and Windows
	// wheels (PIL.features.version("zlib") ends in ".zlib-ng").
	ZlibNG Zlib = iota
	// ZlibClassic is classic zlib 1.2.12 through 1.3.x, which Pillow's
	// manylinux wheels load from the system: the Django Docker image.
	ZlibClassic
)

// Render returns the bytes of the PNG Django stores for text where Pillow
// uses its bundled zlib-ng, or ErrUnsupported. It is RenderWith(text, ZlibNG).
func Render(text string) ([]byte, error) { return RenderWith(text, ZlibNG) }

// RenderWith returns the bytes of the PNG Django stores for text where
// Pillow's PNG encoder compresses with z, or ErrUnsupported.
func RenderWith(text string, z Zlib) ([]byte, error) {
	if z != ZlibNG && z != ZlibClassic {
		return nil, ErrUnsupported
	}
	if !utf8.ValidString(text) {
		return nil, ErrUnsupported
	}
	font := loadFont()

	// One byte per pixel: with a white background and black ink the three
	// channels are always equal.
	var canvas [canvasHeight][canvasWidth]byte
	for y := range canvas {
		for x := range canvas[y] {
			canvas[y][x] = 255
		}
	}

	textWidth := int64(0)
	y := int64(originY)
	for _, line := range strings.Split(text, "\n") {
		glyphs := font.glyphsOf(line)
		if len(glyphs) > maxStringLength {
			return nil, ErrUnsupported
		}
		box := font.measure(glyphs)
		if bombError(box.width, box.height) {
			return nil, ErrUnsupported
		}
		if box.width > textWidth {
			textWidth = box.width
		}
		font.draw(&canvas, glyphs, box, originX, y)
		y += lineStep
	}

	width := textWidth + marginX
	height := y + marginY
	if bombError(width, height) {
		return nil, ErrUnsupported
	}

	// Image.crop: pixels outside the canvas are zero-filled.
	w := int(width)
	row := func(y int, rgb []byte) {
		clear(rgb)
		if y >= canvasHeight {
			return
		}
		for x := 0; x < w && x < canvasWidth; x++ {
			v := canvas[y][x]
			rgb[3*x], rgb[3*x+1], rgb[3*x+2] = v, v, v
		}
	}
	return encodePNG(w, int(height), row, z), nil
}

// bombError mirrors the raising branch of Image._decompression_bomb_check.
func bombError(width, height int64) bool {
	return max(1, width)*max(1, height) > 2*maxImagePixels
}

// pixel mirrors the PIXEL macro of _imagingft.c: 26.6 fixed point rounded to
// the nearest pixel.
func pixel(x int64) int64 { return (x + 32) >> 6 }

// textBox is the result of bounding_box_and_anchors for anchor "la".
type textBox struct {
	width, height    int64
	xOffset, yOffset int64
}

// measure mirrors bounding_box_and_anchors (font_getsize) for horizontal text
// and the default anchor: the box spans the pen line and every glyph's
// control box, and the y offset places the ascender at the anchor.
func (f *fontData) measure(glyphs []uint16) textBox {
	var position, xMin, xMax, yMin, yMax int64
	for _, id := range glyphs {
		g := &f.glyphs[id]
		px := pixel(position)
		position += int64(g.advance)
		xMax = max(xMax, pixel(position), px+int64(g.cboxXMax))
		xMin = min(xMin, px+int64(g.cboxXMin))
		yMax = max(yMax, int64(g.cboxYMax))
		yMin = min(yMin, int64(g.cboxYMin))
	}
	box := textBox{width: xMax - xMin, height: yMax - yMin}
	if len(glyphs) > 0 {
		box.xOffset = xMin
		box.yOffset = pixel(int64(f.ascender)) - yMax
	}
	return box
}

// draw mirrors ImageDraw.text for one line: font_render builds an "L" mask of
// the measured size, and ImagingFill2 blends black through it at
// (x, y) + offset, clipped to the canvas. Only the mask columns that can land
// on the canvas are materialised.
func (f *fontData) draw(canvas *[canvasHeight][canvasWidth]byte, glyphs []uint16, box textBox, x, y int64) {
	if len(glyphs) == 0 || box.width == 0 || box.height == 0 {
		return
	}
	dx0 := x + box.xOffset
	dy0 := y + box.yOffset
	if dy0 >= canvasHeight || dx0 >= canvasWidth {
		return
	}
	cols := min(box.width, canvasWidth-dx0)
	rows := box.height
	mask := make([]byte, cols*rows)

	// font_render: the text origin follows from the rendered bitmaps.
	var pen, xMin, yMax int64
	for _, id := range glyphs {
		g := &f.glyphs[id]
		px := pixel(pen)
		yMax = max(yMax, int64(g.top))
		xMin = min(xMin, int64(g.left)+px)
		pen += int64(g.advance)
	}

	pen = -xMin * 64
	py := pixel(-yMax * 64)
	for _, id := range glyphs {
		g := &f.glyphs[id]
		xx := pixel(pen) + int64(g.left)
		pen += int64(g.advance)
		if xx >= cols {
			// Advances are never negative, so nothing later can reach back
			// further than the widest negative bearing.
			if xx+f.minLeft >= cols {
				break
			}
			continue
		}
		if len(g.bitmap) == 0 {
			continue
		}
		yy := -(py + int64(g.top))
		gw := int64(g.width)
		x0, x1 := int64(0), gw
		if xx < 0 {
			x0 = -xx
		}
		// Clip to the real mask width, then to the materialised columns.
		if xx+x1 > box.width {
			x1 = box.width - xx
		}
		if xx+x1 > cols {
			x1 = cols - xx
		}
		for by := int64(0); by < int64(g.rows); by, yy = by+1, yy+1 {
			if yy < 0 || yy >= rows {
				continue
			}
			source := g.bitmap[by*gw : (by+1)*gw]
			target := mask[yy*cols:]
			for k := x0; k < x1; k++ {
				src := uint32(source[k])
				if src == 0 {
					continue
				}
				if t := uint32(target[xx+k]); t > 0 {
					src = min(255, src+mulDiv255(t, 255-src))
				}
				target[xx+k] = byte(src)
			}
		}
	}

	// ImagingFill2 + fill_mask_L with ink 0: out = BLEND(mask, out, 0).
	sx0, sy0 := int64(0), int64(0)
	xsize, ysize := cols, rows
	if dx0 < 0 {
		xsize += dx0
		if xsize <= 0 {
			return
		}
		sx0, dx0 = -dx0, 0
	}
	if dy0 < 0 {
		ysize += dy0
		if ysize <= 0 {
			return
		}
		sy0, dy0 = -dy0, 0
	}
	ysize = min(ysize, canvasHeight-dy0)
	for yy := int64(0); yy < ysize; yy++ {
		out := canvas[dy0+yy][dx0 : dx0+xsize]
		m := mask[(sy0+yy)*cols+sx0:]
		for i := range out {
			out[i] = byte(div255(uint32(out[i]) * (255 - uint32(m[i]))))
		}
	}
}

// div255 mirrors DIV255 of ImagingUtils.h.
func div255(a uint32) uint32 {
	a += 128
	return ((a >> 8) + a) >> 8
}

// mulDiv255 mirrors MULDIV255.
func mulDiv255(a, b uint32) uint32 { return div255(a * b) }

// ZlibSetting is the environment variable that names the zlib behind the
// Django side's Pillow: "zlib" (classic; the default, as in the Debian image)
// or "zlib-ng" (Pillow's macOS and Windows wheels).
const ZlibSetting = "PIL_PLACEHOLDER_ZLIB"

// ParseZlib maps a ZlibSetting value to its Zlib.
func ParseZlib(value string) (Zlib, bool) {
	switch value {
	case "", "zlib":
		return ZlibClassic, true
	case "zlib-ng":
		return ZlibNG, true
	}
	return ZlibClassic, false
}
