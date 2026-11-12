package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrBMPDecode = errors.New("login BMP pixel decoding failed")

// DecodeBMP normalizes raw and RLE pixels without retaining palette colors.
// Mode 1 is unpacked to 0/255. The budget counts input plus output, not RSS.
func DecodeBMP(ctx context.Context, h BMPHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	c := 0
	switch h.Mode {
	case "1", "L", "P":
		c = 1
	case "RGB":
		c = 3
	case "RGBA":
		c = 4
	default:
		return empty, ErrBMPDecode
	}
	size, err := rasterBytes(h.Width, h.Height, c)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > maxBytes-int64(len(data)) {
		return empty, ErrWorkingLimit
	}
	if h.InvalidPalette || h.DataOffset < 0 || h.DataOffset >= len(data) {
		return empty, ErrBMPDecode
	}
	if h.Compression == 1 || h.Compression == 2 {
		if h.Mode != "1" && h.Mode != "L" && h.Mode != "P" {
			return empty, ErrBMPDecode
		}
		out := make([]byte, int(size))
		if err := bmpRLE(ctx, h, data, out); err != nil {
			return empty, err
		}
		return Raster{h.Width, h.Height, h.Mode, out}, nil
	}
	if h.Compression != 0 && h.Compression != 3 {
		return empty, ErrBMPDecode
	}
	bits := 0
	expectedMode := ""
	switch h.RawMode {
	case "1":
		bits = 1
		expectedMode = "1"
	case "P;1":
		bits = 1
		expectedMode = "P"
	case "P;4":
		bits = 4
		expectedMode = "P"
	case "P", "L":
		bits = 8
		expectedMode = h.RawMode
	case "BGR;15", "BGR;16":
		bits = 16
		expectedMode = "RGB"
	case "BGR":
		bits = 24
		expectedMode = "RGB"
	case "BGRX", "XBGR", "BGXR":
		bits = 32
		expectedMode = "RGB"
	case "RGBA", "BGRA", "ABGR", "BGAR":
		bits = 32
		expectedMode = "RGBA"
	}
	if bits == 0 || h.Mode != expectedMode {
		return empty, ErrBMPDecode
	}
	sourceSize, err := rasterBytes(h.Width, 1, bits)
	if err != nil || sourceSize > int64(^uint(0)>>1)-7 {
		return empty, ErrWorkingLimit
	}
	row := (sourceSize + 7) / 8
	if h.Stride < 0 || int64(h.Stride) < row {
		return empty, ErrBMPDecode
	}
	available := int64(len(data) - h.DataOffset)
	if row > available || int64(h.Height-1) > (available-row)/int64(h.Stride) {
		return empty, ErrBMPDecode
	}
	out := make([]byte, int(size))
	for y := 0; y < h.Height; y++ {
		dy := y
		if !h.TopDown {
			dy = h.Height - 1 - y
		}
		src := data[h.DataOffset+y*h.Stride:]
		dst := out[dy*h.Width*c:]
		for x := 0; x < h.Width; x++ {
			if x&4095 == 0 {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
			}
			switch h.RawMode {
			case "1", "P;1":
				v := (src[x/8] >> uint(7-x%8)) & 1
				if h.Mode == "1" {
					v *= 255
				}
				dst[x] = v
			case "P;4":
				dst[x] = (src[x/2] >> uint(4-4*(x%2))) & 15
			case "P", "L":
				dst[x] = src[x]
			case "BGR;15", "BGR;16":
				v := int(binary.LittleEndian.Uint16(src[x*2:]))
				gmax, shift := 31, 10
				if h.RawMode == "BGR;16" {
					gmax = 63
					shift = 11
				}
				dst[x*3] = byte(((v >> shift) & 31) * 255 / 31)
				dst[x*3+1] = byte(((v >> 5) & gmax) * 255 / gmax)
				dst[x*3+2] = byte((v & 31) * 255 / 31)
			default:
				for j, k := range h.RawMode {
					switch k {
					case 'R':
						dst[x*c] = src[x*(bits/8)+j]
					case 'G':
						dst[x*c+1] = src[x*(bits/8)+j]
					case 'B':
						dst[x*c+2] = src[x*(bits/8)+j]
					case 'A':
						dst[x*c+3] = src[x*(bits/8)+j]
					}
				}
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, h.Mode, out}, nil
}

func bmpRLE(ctx context.Context, h BMPHeader, data, out []byte) error {
	pos, count, x := h.DataOffset, 0, 0
	// Write directly in display row order, clipping surplus decoded bytes just
	// as Pillow's final raw decoder does. No unbounded intermediate RLE buffer.
	put := func(value byte) {
		if count < len(out) {
			y, col := count/h.Width, count%h.Width
			if !h.TopDown {
				y = h.Height - 1 - y
			}
			if h.Mode == "1" && value != 0 {
				value = 255
			}
			out[y*h.Width+col] = value
			count++
		}
	}
	zeros := func(n int) { count += min(n, len(out)-count) } // Output starts zeroed.
	for count < len(out) {
		if err := ctx.Err(); err != nil {
			return err
		}
		if pos > len(data)-2 {
			break
		}
		n, v := int(data[pos]), int(data[pos+1])
		pos += 2
		if n != 0 {
			n = min(n, max(0, h.Width-x))
			for i := 0; i < n; i++ {
				value := v
				if h.Compression == 2 {
					value = (v >> uint(4-4*(i%2))) & 15
				}
				put(byte(value))
			}
			x += n
			continue
		}
		switch v {
		case 0:
			if count%h.Width != 0 {
				zeros(h.Width - count%h.Width)
			}
			x = 0
		case 1:
			if count < len(out) {
				return ErrBMPDecode
			}
		case 2:
			// Delta is exactly one pair: right followed by up.
			if pos > len(data)-2 {
				return ErrBMPDecode
			}
			rows := int(data[pos+1])
			if rows > (len(out)-count)/h.Width {
				count = len(out)
			} else {
				zeros(rows * h.Width)
				zeros(int(data[pos]))
			}
			pos += 2
			x = count % h.Width
		default:
			dataCount := min(v, len(out)-count)
			n := dataCount
			if h.Compression == 2 {
				n = (dataCount + 1) / 2
			}
			actual := min(n, len(data)-pos)
			for i, b := range data[pos : pos+actual] {
				if h.Compression == 2 {
					put(b >> 4)
					if i*2+1 < dataCount {
						put(b & 15)
					}
				} else {
					put(b)
				}
			}
			pos += actual
			if actual < n {
				if count < len(out) {
					return ErrBMPDecode
				}
				break
			}
			x += v
			if pos%2 != 0 {
				pos++
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if count < len(out) {
		return ErrBMPDecode
	}
	return nil
}
