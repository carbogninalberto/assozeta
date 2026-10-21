package loginimage

import (
	"bytes"
	"context"
	"errors"
)

var ErrXBMDecode = errors.New("login XBM pixel decoding failed")

func xbmHex(v byte) byte {
	switch {
	case v >= '0' && v <= '9':
		return v - '0'
	case v >= 'A' && v <= 'F':
		return v - 'A' + 10
	case v >= 'a' && v <= 'f':
		return v - 'a' + 10
	default:
		return 0
	}
}

// DecodeXBM follows the reference's lowercase-x scanner, permissive hex operands
// and least-significant-bit-first row packing. Pixels are normalized to 0/255.
// Logical budgets count input plus output, not allocator overhead or total RSS.
func DecodeXBM(ctx context.Context, h XBMHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if h.Mode != "1" || h.DataOffset < 0 || h.DataOffset > len(data) {
		return empty, ErrXBMDecode
	}
	size, err := rasterBytes(h.Width, h.Height, 1)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) {
		return empty, ErrWorkingLimit
	}
	out := make([]byte, int(size))
	pos, x, y := h.DataOffset, 0, 0
	for y < h.Height {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		if pos == len(data) {
			return empty, ErrXBMDecode
		}
		end := pos + min(4096, len(data)-pos)
		i := bytes.IndexByte(data[pos:end], 'x')
		if i < 0 {
			pos = end
			continue
		}
		pos += i
		if len(data)-pos < 3 {
			return empty, ErrXBMDecode
		}
		v := (xbmHex(data[pos+1]) << 4) | xbmHex(data[pos+2])
		pos += 3
		n := min(8, h.Width-x)
		for bit := 0; bit < n; bit++ {
			out[y*h.Width+x+bit] = ((v >> uint(bit)) & 1) * 255
		}
		x += n
		if x == h.Width {
			x = 0
			y++
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, "1", out}, nil
}
