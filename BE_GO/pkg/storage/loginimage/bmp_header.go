package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrBMPHeader = errors.New("login BMP header invalid")

// BMPHeader describes standalone BMP and DIB files. Palette indices retain mode
// P; a palette is not needed by the login policy, whose JPEG save rejects P.
type BMPHeader struct {
	Width, Height, Bits, Compression int
	Mode, RawMode, Format            string
	TopDown, PixelWarning            bool
	InvalidPalette                   bool
	Stride, DataOffset, HeaderBytes  int
	CURMaskHeight                    int // Full DIB height for gray CUR AND/XOR planes.
}

func bmpHeaderSize(n uint32) bool {
	switch n {
	case 12, 40, 52, 56, 64, 108, 124:
		return true
	}
	return false
}
func bmpSignature(data []byte) bool {
	return len(data) >= 2 && string(data[:2]) == "BM" || len(data) >= 4 && bmpHeaderSize(binary.LittleEndian.Uint32(data))
}

// ReadBMPHeader preserves Pillow 13's lazy admission, including its palette
// mode reduction and offset quirks. maxBytes bounds consumed header bytes.
func ReadBMPHeader(ctx context.Context, data []byte, maxBytes int) (BMPHeader, error) {
	return readBMPHeader(ctx, data, maxBytes, false)
}

// CUR adjusts the DIB height before Image.open applies its pixel ceiling.
func readBMPHeader(ctx context.Context, data []byte, maxBytes int, halfHeight bool) (BMPHeader, error) {
	empty := BMPHeader{}
	rejected, terminal := ErrBMPHeader, ErrBMPHeader
	if halfHeight {
		rejected, terminal = ErrCURRejected, ErrCURTerminal
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	pos := 0
	read := func(n int, strict bool) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, rejected
		}
		available := min(n, len(data)-pos)
		if available > maxBytes-pos {
			return nil, ErrWorkingLimit
		}
		if strict && available != n {
			return nil, rejected
		}
		out := data[pos : pos+available]
		pos += available
		return out, nil
	}
	h := BMPHeader{Format: "DIB"}
	offset := uint64(0)
	if !halfHeight && len(data) >= 2 && string(data[:2]) == "BM" {
		b, err := read(14, true)
		if err != nil {
			return empty, err
		}
		offset = uint64(binary.LittleEndian.Uint32(b[10:]))
		h.Format = "BMP"
	}
	b, err := read(4, true)
	if err != nil {
		return empty, err
	}
	n := binary.LittleEndian.Uint32(b)
	if !bmpHeaderSize(n) {
		return empty, terminal
	}
	b, err = read(int(n)-4, true)
	if err != nil {
		if errors.Is(err, rejected) {
			return empty, terminal
		}
		return empty, err
	}
	le := binary.LittleEndian
	colors, padding := uint64(0), 4
	var masks [4]uint32
	if n == 12 {
		h.Width = int(le.Uint16(b))
		h.Height = int(le.Uint16(b[2:]))
		h.Bits = int(le.Uint16(b[6:]))
		padding = 3
	} else {
		w, y := uint64(le.Uint32(b)), uint64(le.Uint32(b[4:]))
		h.TopDown = b[7] == 255
		if h.TopDown {
			y = (1 << 32) - y
		}
		if w > uint64(^uint(0)>>1) || y > uint64(^uint(0)>>1) {
			return empty, terminal
		}
		h.Width = int(w)
		h.Height = int(y)
		h.Bits = int(le.Uint16(b[10:]))
		h.Compression = int(le.Uint32(b[12:]))
		colors = uint64(le.Uint32(b[28:]))
		if h.Compression == 3 {
			if len(b) >= 48 {
				for i := 0; i < 3; i++ {
					masks[i] = le.Uint32(b[36+i*4:])
				}
				if len(b) >= 52 {
					masks[3] = le.Uint32(b[48:])
				}
			} else {
				v, e := read(12, true)
				if e != nil {
					return empty, e
				}
				for i := 0; i < 3; i++ {
					masks[i] = le.Uint32(v[i*4:])
				}
			}
		}
	}
	if !halfHeight {
		if h.Width <= 0 || h.Height <= 0 || uint64(h.Width)*uint64(h.Height) > 178956970 {
			return empty, terminal
		}
		h.PixelWarning = uint64(h.Width)*uint64(h.Height) > 89478485
	}
	switch h.Bits {
	case 1:
		h.Mode = "P"
		h.RawMode = "P;1"
	case 4:
		h.Mode = "P"
		h.RawMode = "P;4"
	case 8:
		h.Mode = "P"
		h.RawMode = "P"
	case 16:
		h.Mode = "RGB"
		h.RawMode = "BGR;15"
	case 24:
		h.Mode = "RGB"
		h.RawMode = "BGR"
	case 32:
		h.Mode = "RGB"
		h.RawMode = "BGRX"
	default:
		return empty, terminal
	}
	if colors == 0 {
		colors = uint64(1) << h.Bits
	}
	if offset == 14+uint64(n) && h.Bits <= 8 {
		offset += uint64(padding) * colors
	}
	switch h.Compression {
	case 0, 1, 2:
	case 3:
		h.RawMode = bmpMaskMode(h.Bits, masks)
		if h.RawMode == "" {
			return empty, terminal
		}
		for _, c := range h.RawMode {
			if c == 'A' {
				h.Mode = "RGBA"
			}
		}
	default:
		return empty, terminal
	}
	if h.Mode == "P" {
		// Pillow compares each entry with o8(index), wrapping at 256.
		if colors > 65536 {
			return empty, terminal
		}
		palette, e := read(padding*int(colors), false)
		if e != nil {
			return empty, e
		}
		gray := true
		for i := 0; i < int(colors); i++ {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			v := byte(i)
			if colors == 2 && i == 1 {
				v = 255
			}
			j := i * padding
			if j+3 > len(palette) || palette[j] != v || palette[j+1] != v || palette[j+2] != v {
				gray = false
			}
		}
		if gray {
			h.Mode = "L"
			if colors <= 2 {
				h.Mode = "1"
			}
			h.RawMode = h.Mode
		} else {
			// The lazy open accepts oversized palettes; Image.load rejects
			// them only when the raw palette has more than 256 whole entries.
			h.InvalidPalette = len(palette)/padding > 256
		}
	}
	// The CUR driver completes DIB admission before halving height. ImageFile
	// rejects zero dimensions recoverably; Image.open's bomb ceiling is terminal.
	if halfHeight {
		if h.Mode == "1" || h.Mode == "L" {
			h.CURMaskHeight = h.Height
			h.Mode = "LA"
		}
		h.Height /= 2
		if h.Width <= 0 || h.Height <= 0 {
			return empty, rejected
		}
		if uint64(h.Width)*uint64(h.Height) > 178956970 {
			return empty, terminal
		}
		h.PixelWarning = uint64(h.Width)*uint64(h.Height) > 89478485
	}
	stride := ((uint64(h.Width)*uint64(h.Bits) + 31) >> 3) &^ uint64(3)
	if offset == 0 {
		offset = uint64(pos)
	}
	if stride > uint64(^uint(0)>>1) || offset > uint64(^uint(0)>>1) {
		return empty, terminal
	}
	h.Stride = int(stride)
	h.DataOffset = int(offset)
	h.HeaderBytes = pos
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return h, nil
}

func bmpMaskMode(bits int, m [4]uint32) string {
	if bits == 16 {
		if m[0] == 0xf800 && m[1] == 0x7e0 && m[2] == 0x1f {
			return "BGR;16"
		}
		if m[0] == 0x7c00 && m[1] == 0x3e0 && m[2] == 0x1f {
			return "BGR;15"
		}
	}
	if bits == 24 && m[0] == 0xff0000 && m[1] == 0xff00 && m[2] == 0xff {
		return "BGR"
	}
	if bits == 32 {
		switch m {
		case [4]uint32{0xff0000, 0xff00, 0xff, 0}:
			return "BGRX"
		case [4]uint32{0xff000000, 0xff0000, 0xff00, 0}:
			return "XBGR"
		case [4]uint32{0xff000000, 0xff00, 0xff, 0}:
			return "BGXR"
		case [4]uint32{0xff000000, 0xff0000, 0xff00, 0xff}:
			return "ABGR"
		case [4]uint32{0xff, 0xff00, 0xff0000, 0xff000000}:
			return "RGBA"
		case [4]uint32{0xff0000, 0xff00, 0xff, 0xff000000}, [4]uint32{}:
			return "BGRA"
		case [4]uint32{0xff000000, 0xff00, 0xff, 0xff0000}:
			return "BGAR"
		}
	}
	return ""
}
