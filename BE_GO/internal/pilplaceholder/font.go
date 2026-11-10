package pilplaceholder

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/binary"
	"io"
	"sync"
)

// fontdata.bin.gz holds what FreeType 2.14.3 (the build bundled with Pillow
// 13.0.0) returns for Pillow's embedded Aileron Regular after
// FT_Request_Size(nominal, 10 px): for every glyph index the result of
// FT_Load_Glyph(FT_LOAD_DEFAULT) and FT_LOAD_RENDER, and FT_Get_Char_Index for
// every code point. Layout, little endian:
//
//	"PILFNT1\x00"
//	int32  ascender (26.6)
//	uint16 glyph count, then per glyph:
//	  int32  horiAdvance (26.6)
//	  int16  bitmap_left, bitmap_top
//	  uint16 bitmap width, rows
//	  int16  control box xMin, xMax, yMin, yMax (pixels)
//	  width*rows coverage bytes
//	uint32 mapping count, then per mapped code point (ascending):
//	  uint32 code point, uint16 glyph index
//
//go:embed fontdata.bin.gz
var fontBlob []byte

type glyph struct {
	advance                                int32
	left, top                              int16
	width, rows                            uint16
	cboxXMin, cboxXMax, cboxYMin, cboxYMax int16
	bitmap                                 []byte
}

type fontData struct {
	ascender int32
	glyphs   []glyph
	cmap     map[rune]uint16
	minLeft  int64
}

var loadFont = sync.OnceValue(func() *fontData {
	zr, err := gzip.NewReader(bytes.NewReader(fontBlob))
	if err != nil {
		panic("pilplaceholder: font data: " + err.Error())
	}
	raw, err := io.ReadAll(zr)
	if err != nil {
		panic("pilplaceholder: font data: " + err.Error())
	}
	const magic = "PILFNT1\x00"
	if len(raw) < len(magic) || string(raw[:len(magic)]) != magic {
		panic("pilplaceholder: font data: bad magic")
	}
	p := raw[len(magic):]
	u16 := func() uint16 {
		v := binary.LittleEndian.Uint16(p)
		p = p[2:]
		return v
	}
	u32 := func() uint32 {
		v := binary.LittleEndian.Uint32(p)
		p = p[4:]
		return v
	}
	f := &fontData{ascender: int32(u32())}
	f.glyphs = make([]glyph, u16())
	for i := range f.glyphs {
		g := &f.glyphs[i]
		g.advance = int32(u32())
		g.left, g.top = int16(u16()), int16(u16())
		g.width, g.rows = u16(), u16()
		g.cboxXMin, g.cboxXMax = int16(u16()), int16(u16())
		g.cboxYMin, g.cboxYMax = int16(u16()), int16(u16())
		n := int(g.width) * int(g.rows)
		g.bitmap, p = p[:n:n], p[n:]
		if g.advance < 0 {
			panic("pilplaceholder: font data: negative advance")
		}
		f.minLeft = min(f.minLeft, int64(g.left))
	}
	n := int(u32())
	f.cmap = make(map[rune]uint16, n)
	for ; n > 0; n-- {
		cp := rune(u32())
		f.cmap[cp] = u16()
	}
	if len(p) != 0 {
		panic("pilplaceholder: font data: trailing bytes")
	}
	return f
})

// glyphsOf mirrors text_layout_fallback: one glyph per code point through
// FT_Get_Char_Index, glyph 0 (.notdef) for anything the font does not map.
// There is no shaping: control characters, combining marks and tabs are
// ordinary code points.
func (f *fontData) glyphsOf(line string) []uint16 {
	glyphs := make([]uint16, 0, len(line))
	for _, r := range line {
		glyphs = append(glyphs, f.cmap[r])
	}
	return glyphs
}
