package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrTGAHeader = errors.New("login TGA header invalid")

// TGAHeader records the lazy reader state, before palette application and load.
// Empty RawMode is an admitted header without a supported pixel tile.
type TGAHeader struct {
	Width, Height, Depth, ImageType, DataOffset                                   int
	Mode, RawMode                                                                 string
	TopDown, FlipHorizontal, Compressed, HasPalette, InvalidPalette, PixelWarning bool
}

func tgaSignature(b []byte) bool {
	if len(b) < 18 || b[1] > 1 || binary.LittleEndian.Uint16(b[12:]) == 0 || binary.LittleEndian.Uint16(b[14:]) == 0 {
		return false
	}
	switch b[2] {
	case 1, 2, 3, 9, 10, 11:
	default:
		return false
	}
	switch b[16] {
	case 1, 8, 16, 24, 32:
		return true
	}
	return false
}

// ReadTGAHeader follows Pillow 13's short ID/palette reads. The budget counts
// consumed input bytes; a palette's implicit zero prefix is never allocated.
func ReadTGAHeader(ctx context.Context, data []byte, maxBytes int) (TGAHeader, error) {
	empty := TGAHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	pos := 0
	read := func(n int) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		n = min(n, len(data)-pos)
		if n > maxBytes-pos {
			return nil, ErrWorkingLimit
		}
		b := data[pos : pos+n]
		pos += n
		return b, nil
	}
	b, err := read(18)
	if err != nil {
		return empty, err
	}
	if !tgaSignature(b) {
		return empty, ErrTGAHeader
	}
	le := binary.LittleEndian
	h := TGAHeader{Width: int(le.Uint16(b[12:])), Height: int(le.Uint16(b[14:])), Depth: int(b[16]), ImageType: int(b[2]), TopDown: b[17]&32 != 0, FlipHorizontal: b[17]&16 != 0, Compressed: b[2]&8 != 0, HasPalette: b[1] == 1}
	pixels := uint64(h.Width) * uint64(h.Height)
	if pixels > 178956970 {
		return empty, ErrTGAHeader
	}
	h.PixelWarning = pixels > 89478485
	switch h.ImageType & 7 {
	case 1:
		h.Mode = "L"
		if h.HasPalette {
			h.Mode = "P"
		}
		if h.Depth == 8 {
			h.RawMode = "P"
		}
	case 2:
		h.Mode = "RGBA"
		switch h.Depth {
		case 16:
			h.RawMode = "BGRA;15Z"
		case 24:
			h.Mode = "RGB"
			h.RawMode = "BGR"
		case 32:
			h.RawMode = "BGRA"
		}
	case 3:
		h.Mode = "L"
		switch h.Depth {
		case 1:
			h.Mode = "1"
			h.RawMode = "1"
		case 8:
			h.RawMode = "L"
		case 16:
			h.Mode = "LA"
			h.RawMode = "LA"
		}
	}
	if _, err = read(int(b[0])); err != nil {
		return empty, err
	}
	if h.HasPalette {
		unit := 0
		switch b[7] {
		case 16:
			unit = 2
		case 24:
			unit = 3
		case 32:
			unit = 4
		default:
			return empty, ErrTGAHeader
		}
		palette, err := read(unit * int(le.Uint16(b[5:])))
		if err != nil {
			return empty, err
		}
		h.InvalidPalette = int(le.Uint16(b[3:]))+len(palette)/unit > 256
	}
	h.DataOffset = pos
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return h, nil
}
