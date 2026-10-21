package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrTGADecode = errors.New("login TGA pixel decoding failed")

// DecodeTGA normalizes raw/RLE pixels, their orientation and inverted 16-bit
// alpha. The logical budget counts input plus output, not aggregate RSS. Palette
// application can change gray L/LA to core P/PA, which JPEG cannot encode.
func DecodeTGA(ctx context.Context, h TGAHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	c, depth, mode := 1, 0, ""
	switch h.RawMode {
	case "1":
		depth = 1
		mode = "1"
	case "L", "P":
		depth = 8
		mode = h.RawMode
	case "LA":
		depth = 16
		c = 2
		mode = "LA"
	case "BGRA;15Z":
		depth = 16
		c = 4
		mode = "RGBA"
	case "BGR":
		depth = 24
		c = 3
		mode = "RGB"
	case "BGRA":
		depth = 32
		c = 4
		mode = "RGBA"
	}
	if mode == "" || h.Mode != mode || h.Depth != depth || h.InvalidPalette || h.DataOffset < 0 || h.DataOffset > len(data) {
		return empty, ErrTGADecode
	}
	if h.HasPalette {
		switch mode {
		case "L":
			mode = "P"
		case "LA":
			mode = "PA"
		case "P":
		default:
			return empty, ErrTGADecode
		}
	}
	if h.RawMode == "P" && !h.HasPalette || h.Compressed && depth == 1 {
		return empty, ErrTGADecode
	}
	size, err := rasterBytes(h.Width, h.Height, c)
	if err != nil {
		return empty, err
	}
	packed, err := rasterBytes(h.Width, 1, depth)
	if err != nil || packed > int64(^uint(0)>>1)-7 {
		return empty, ErrWorkingLimit
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) {
		return empty, ErrWorkingLimit
	}
	out := make([]byte, int(size))
	put := func(i int, src []byte) {
		x, y := i%h.Width, i/h.Width
		if !h.TopDown {
			y = h.Height - 1 - y
		}
		if h.FlipHorizontal {
			x = h.Width - 1 - x
		}
		dst := out[(y*h.Width+x)*c:]
		switch h.RawMode {
		case "1", "L", "P":
			dst[0] = src[0]
		case "LA":
			copy(dst[:2], src[:2])
		case "BGR", "BGRA":
			dst[0] = src[2]
			dst[1] = src[1]
			dst[2] = src[0]
			if c == 4 {
				dst[3] = src[3]
			}
		case "BGRA;15Z":
			v := binary.LittleEndian.Uint16(src)
			dst[0] = byte(((v >> 10) & 31) * 255 / 31)
			dst[1] = byte(((v >> 5) & 31) * 255 / 31)
			dst[2] = byte((v & 31) * 255 / 31)
			dst[3] = 255 - byte(v>>15)*255
		}
	}
	pos, unit, total := h.DataOffset, depth/8, int(size)/c
	if h.Compressed {
		for i := 0; i < total; {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			if pos == len(data) {
				return empty, ErrTGADecode
			}
			packet := data[pos]
			pos++
			n := int(packet&127) + 1
			if packet&128 != 0 {
				if n > h.Width-i%h.Width || unit > len(data)-pos {
					return empty, ErrTGADecode
				}
				for j := 0; j < n; j++ {
					put(i+j, data[pos:pos+unit])
				}
				pos += unit
				i += n
			} else {
				if n*unit > len(data)-pos {
					return empty, ErrTGADecode
				}
				for j := 0; j < min(n, total-i); j++ {
					put(i+j, data[pos+j*unit:pos+(j+1)*unit])
				}
				pos += n * unit
				i += min(n, total-i)
			}
		}
	} else {
		rowBytes := int((packed + 7) / 8)
		for y := 0; y < h.Height; y++ {
			if rowBytes > len(data)-pos {
				return empty, ErrTGADecode
			}
			row := data[pos : pos+rowBytes]
			pos += rowBytes
			for x := 0; x < h.Width; x++ {
				if x&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return empty, err
					}
				}
				if depth == 1 {
					v := ((row[x/8] >> uint(7-x%8)) & 1) * 255
					put(y*h.Width+x, []byte{v})
				} else {
					put(y*h.Width+x, row[x*unit:(x+1)*unit])
				}
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, mode, out}, nil
}
