package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrPCXHeader = errors.New("login PCX header invalid")

const dcxMagic = 0x3ade68b1

// PCXHeader describes the standalone image or first DCX frame selected by login.
// HeaderBytes counts consumed bytes, including overlapping reads of the palette.
type PCXHeader struct {
	Width, Height, Bits, Planes, Stride, DataOffset, HeaderBytes, Frames int
	Mode, RawMode, Format                                                string
	PixelWarning                                                         bool
}

func pcxPrefix(data []byte) bool {
	return len(data) >= 2 && data[0] == 10 && (data[1] == 0 || data[1] == 2 || data[1] == 3 || data[1] == 5)
}
func pcxSignature(data []byte) bool {
	return pcxPrefix(data) || len(data) >= 4 && binary.LittleEndian.Uint32(data) == dcxMagic
}

// ReadPCXHeader follows Pillow 11.1's PCX/DCX lazy admission. Byte budgets include
// the DCX directory, partial 128-byte header and whole-file tail palette reads.
func ReadPCXHeader(ctx context.Context, data []byte, maxBytes int) (PCXHeader, error) {
	empty := PCXHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	used := 0
	read := func(offset, n int) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if offset < 0 || n < 0 {
			return nil, ErrPCXHeader
		}
		if offset > len(data) {
			return nil, nil
		}
		n = min(n, len(data)-offset)
		if n > maxBytes-used {
			return nil, ErrWorkingLimit
		}
		used += n
		return data[offset : offset+n], nil
	}
	h := PCXHeader{Format: "PCX", Frames: 1}
	start := 0
	if len(data) >= 4 && binary.LittleEndian.Uint32(data) == dcxMagic {
		h.Format = "DCX"
		h.Frames = 0
		if _, err := read(0, 4); err != nil {
			return empty, err
		}
		for i := 0; i < 1024; i++ {
			b, err := read(4+i*4, 4)
			if err != nil {
				return empty, err
			}
			if len(b) != 4 {
				return empty, ErrPCXHeader
			}
			offset := binary.LittleEndian.Uint32(b)
			if offset == 0 {
				break
			}
			if i == 0 {
				if uint64(offset) > uint64(^uint(0)>>1) {
					return empty, ErrPCXHeader
				}
				start = int(offset)
			}
			h.Frames++
		}
		if h.Frames == 0 {
			return empty, ErrPCXHeader
		}
	}
	b, err := read(start, 128)
	if err != nil {
		return empty, err
	}
	if len(b) < 68 || !pcxPrefix(b) {
		return empty, ErrPCXHeader
	}
	le := binary.LittleEndian
	h.Width = int(le.Uint16(b[8:])) + 1 - int(le.Uint16(b[4:]))
	h.Height = int(le.Uint16(b[10:])) + 1 - int(le.Uint16(b[6:]))
	if h.Width <= 0 || h.Height <= 0 || uint64(h.Width)*uint64(h.Height) > 178956970 {
		return empty, ErrPCXHeader
	}
	h.PixelWarning = uint64(h.Width)*uint64(h.Height) > 89478485
	h.Bits = int(b[3])
	h.Planes = int(b[65])
	provided := int(le.Uint16(b[66:]))
	h.DataOffset = start + len(b)
	switch {
	case h.Bits == 1 && h.Planes == 1:
		h.Mode = "1"
		h.RawMode = "1"
	case h.Bits == 1 && h.Planes == 2:
		h.Mode = "P"
		h.RawMode = "P;2L"
	case h.Bits == 1 && h.Planes == 4:
		h.Mode = "P"
		h.RawMode = "P;4L"
	case b[1] == 5 && h.Bits == 8 && h.Planes == 1:
		h.Mode = "L"
		h.RawMode = "L"
		// BytesIO clamps negative SEEK_END positions to zero. DCX inherits
		// this whole-file palette lookup and absolute pixel seek to 128.
		palette, err := read(max(0, len(data)-769), 769)
		if err != nil {
			return empty, err
		}
		if len(palette) == 769 && palette[0] == 12 {
			for i := 0; i < 256; i++ {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
				j := 1 + i*3
				if palette[j] != byte(i) || palette[j+1] != byte(i) || palette[j+2] != byte(i) {
					h.Mode = "P"
					h.RawMode = "P"
					break
				}
			}
		}
		h.DataOffset = 128
	case b[1] == 5 && h.Bits == 8 && h.Planes == 3:
		h.Mode = "RGB"
		h.RawMode = "RGB;L"
	default:
		return empty, ErrPCXHeader
	}
	stride := (h.Width*h.Bits + 7) / 8
	if provided != stride {
		stride += stride % 2
	}
	h.Stride = h.Planes * stride
	h.HeaderBytes = used
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return h, nil
}
