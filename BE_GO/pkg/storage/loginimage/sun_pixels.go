package loginimage

import (
	"context"
	"errors"
	"math"
)

var ErrSUNDecode = errors.New("login SUN pixel decoding failed")

// DecodeSUN normalizes packed monochrome/nibble samples and RGB channel order.
// Raw rows use 16-bit alignment; byte-RLE rows use only their packed byte count.
// Logical budgets count input, output and one packed RLE row (not total RSS).
func DecodeSUN(ctx context.Context, h SUNHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	mode, raw := sunModes(h.Depth, h.FileType, h.HasPalette)
	if mode == "" || h.Mode != mode || h.RawMode != raw || h.FileType < 0 || h.FileType > 5 || h.InvalidPalette || h.HasPalette && mode != "P" || h.DataOffset < 32 || h.DataOffset > len(data) {
		return empty, ErrSUNDecode
	}
	channels := 1
	if mode == "RGB" {
		channels = 3
	}
	size, err := rasterBytes(h.Width, h.Height, channels)
	if err != nil {
		return empty, err
	}
	if int64(h.Width) > (math.MaxInt64-15)/int64(h.Depth) {
		return empty, ErrWorkingLimit
	}
	bits := int64(h.Width) * int64(h.Depth)
	rowBytes, stride := (bits+7)/8, ((bits+15)/16)*2
	if stride > int64(^uint(0)>>1) {
		return empty, ErrWorkingLimit
	}
	if int64(h.Stride) != stride {
		return empty, ErrSUNDecode
	}
	scratch := int64(0)
	if h.FileType == 2 {
		scratch = rowBytes
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || int64(len(data)) > maxBytes || scratch > maxBytes-int64(len(data)) || size > maxBytes-int64(len(data))-scratch {
		return empty, ErrWorkingLimit
	}
	remaining := int64(len(data) - h.DataOffset)
	if h.FileType != 2 && (int64(h.Height-1) > remaining/stride || rowBytes > remaining-int64(h.Height-1)*stride) {
		return empty, ErrSUNDecode
	}
	if remaining == 0 {
		return empty, ErrSUNDecode
	}
	out := make([]byte, int(size))
	unpack := func(y int, row []byte) error {
		for x := 0; x < h.Width; x++ {
			if x%4096 == 0 {
				if err := ctx.Err(); err != nil {
					return err
				}
			}
			dst := (y*h.Width + x) * channels
			switch h.Depth {
			case 1:
				out[dst] = 255 - ((row[x/8]>>uint(7-x%8))&1)*255
			case 4:
				shift := uint(4)
				if x%2 == 1 {
					shift = 0
				}
				v := (row[x/2] >> shift) & 15
				if mode == "L" {
					v *= 17
				}
				out[dst] = v
			case 8:
				out[dst] = row[x]
			case 24, 32:
				src := x * (h.Depth / 8)
				if h.FileType == 3 {
					copy(out[dst:dst+3], row[src:src+3])
				} else {
					out[dst] = row[src+2]
					out[dst+1] = row[src+1]
					out[dst+2] = row[src]
				}
			}
		}
		return nil
	}
	if h.FileType != 2 {
		for y := 0; y < h.Height; y++ {
			start := h.DataOffset + y*h.Stride
			if err := unpack(y, data[start:start+int(rowBytes)]); err != nil {
				return empty, err
			}
		}
	} else {
		row := make([]byte, int(rowBytes))
		x, y, pos := 0, 0, h.DataOffset
		for y < h.Height {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			if pos == len(data) {
				return empty, ErrSUNDecode
			}
			value, count := data[pos], 1
			pos++
			if value == 128 {
				if pos == len(data) {
					return empty, ErrSUNDecode
				}
				n := int(data[pos])
				pos++
				if n > 0 {
					if pos == len(data) {
						return empty, ErrSUNDecode
					}
					count, value = n+1, data[pos]
					pos++
				}
			}
			for count > 0 && y < h.Height {
				n := min(count, len(row)-x)
				for i := 0; i < n; i++ {
					row[x+i] = value
				}
				x += n
				count -= n
				if x == len(row) {
					if err := unpack(y, row); err != nil {
						return empty, err
					}
					x = 0
					y++
				}
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, mode, out}, nil
}
