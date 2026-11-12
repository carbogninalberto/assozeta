package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrSUNHeader = errors.New("login SUN header invalid")

type SUNHeader struct {
	Width, Height, Depth, FileType, Stride   int
	DataOffset, HeaderBytes                  int
	Mode, RawMode                            string
	HasPalette, InvalidPalette, PixelWarning bool
}

func sunSignature(data []byte) bool {
	return len(data) >= 4 && binary.BigEndian.Uint32(data) == 0x59a66a95
}
func sunModes(depth, kind int, palette bool) (string, string) {
	switch depth {
	case 1:
		return "1", "1;I"
	case 4:
		if palette {
			return "P", "P;4"
		}
		return "L", "L;4"
	case 8:
		if palette {
			return "P", "P"
		}
		return "L", "L"
	case 24:
		if kind == 3 {
			return "RGB", "RGB"
		}
		return "RGB", "BGR"
	case 32:
		if kind == 3 {
			return "RGB", "RGBX"
		}
		return "RGB", "BGRX"
	}
	return "", ""
}

// ReadSUNHeader preserves the declared palette offset while charging only bytes
// actually read. Palette/core-mode errors remain deferred until pixel loading.
// The unreliable data-length field is ignored, as in Pillow 11.1.0.
func ReadSUNHeader(ctx context.Context, data []byte, maxBytes int) (SUNHeader, error) {
	empty := SUNHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 || min(32, len(data)) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if len(data) < 32 || !sunSignature(data) {
		return empty, ErrSUNHeader
	}
	field := func(n int) uint64 { return uint64(binary.BigEndian.Uint32(data[n:])) }
	w, h, d, kind, ptype, plen := field(4), field(8), field(12), field(20), field(24), field(28)
	if w == 0 || h == 0 || w > 178956970/h || kind > 5 || plen > 1024 || plen > 0 && ptype != 1 {
		return empty, ErrSUNHeader
	}
	mode, raw := sunModes(int(d), int(kind), plen > 0)
	if mode == "" {
		return empty, ErrSUNHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	actual := min(int(plen), len(data)-32)
	if 32+actual > maxBytes {
		return empty, ErrWorkingLimit
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return SUNHeader{Width: int(w), Height: int(h), Depth: int(d), FileType: int(kind), Stride: int(((w*d + 15) / 16) * 2), DataOffset: 32 + int(plen), HeaderBytes: 32 + actual, Mode: mode, RawMode: raw, HasPalette: plen > 0, InvalidPalette: plen > 0 && (mode != "P" || actual/3 > 256), PixelWarning: w*h > 89478485}, nil
}
