package loginimage

import (
	"context"
	"errors"
)

var ErrPCXDecode = errors.New("login PCX pixel decoding failed")

// DecodePCX preserves Pillow 13's PCX RLE and per-plane padding removal.
// Mode 1 becomes 0/255 and P retains indices. The logical
// budget counts the input, normalized output and one decoded row, not total RSS.
func DecodePCX(ctx context.Context, h PCXHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	c, bits, planes, mode := 1, 0, 0, ""
	switch h.RawMode {
	case "1":
		bits = 1
		planes = 1
		mode = "1"
	case "P;2L":
		bits = 1
		planes = 2
		mode = "P"
	case "P;4L":
		bits = 1
		planes = 4
		mode = "P"
	case "L", "P":
		bits = 8
		planes = 1
		mode = h.RawMode
	case "RGB;L":
		bits = 8
		planes = 3
		c = 3
		mode = "RGB"
	}
	if mode == "" || h.Mode != mode || h.Bits != bits || h.Planes != planes {
		return empty, ErrPCXDecode
	}
	size, err := rasterBytes(h.Width, h.Height, c)
	if err != nil {
		return empty, err
	}
	packed, err := rasterBytes(h.Width, 1, bits)
	if err != nil || packed > int64(^uint(0)>>1)-7 {
		return empty, ErrWorkingLimit
	}
	minimum := (packed + 7) / 8
	if minimum > int64(^uint(0)>>1)/int64(planes)-1 {
		return empty, ErrWorkingLimit
	}
	if h.Stride != int(minimum)*planes && h.Stride != int(minimum+minimum%2)*planes {
		return empty, ErrPCXDecode
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) || int64(h.Stride) > maxBytes-int64(len(data))-size {
		return empty, ErrWorkingLimit
	}
	if h.DataOffset < 0 || h.DataOffset >= len(data) {
		return empty, ErrPCXDecode
	}
	out := make([]byte, int(size))
	row := make([]byte, h.Stride)
	pos := h.DataOffset
	for y := 0; y < h.Height; y++ {
		for x := 0; x < len(row); {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			if pos == len(data) {
				return empty, ErrPCXDecode
			}
			v := data[pos]
			pos++
			n := 1
			if v&0xc0 == 0xc0 {
				n = int(v & 63)
				if pos == len(data) {
					return empty, ErrPCXDecode
				}
				v = data[pos]
				pos++
			}
			if n > len(row)-x {
				return empty, ErrPCXDecode
			}
			for i := 0; i < n; i++ {
				row[x+i] = v
			}
			x += n
		}
		planeStride := h.Stride / planes
		dst := out[y*h.Width*c:]
		for x := 0; x < h.Width; x++ {
			if x&4095 == 0 {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
			}
			switch h.RawMode {
			case "1":
				dst[x] = ((row[x/8] >> uint(7-x%8)) & 1) * 255
			case "L", "P":
				dst[x] = row[x]
			case "RGB;L":
				dst[x*3] = row[x]
				dst[x*3+1] = row[x+planeStride]
				dst[x*3+2] = row[x+planeStride*2]
			default:
				v := byte(0)
				for p := 0; p < planes; p++ {
					v |= ((row[p*planeStride+x/8] >> uint(7-x%8)) & 1) << uint(p)
				}
				dst[x] = v
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, h.Mode, out}, nil
}
