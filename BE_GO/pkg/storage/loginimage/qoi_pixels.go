package loginimage

import (
	"context"
	"errors"
)

var ErrQOIDecode = errors.New("login QOI pixel decoding failed")

// DecodeQOI preserves Pillow's seeded color cache, wrapping deltas, clipped runs
// and ignored end marker. The logical budget counts input plus normalized output;
// the fixed 64-entry cache and parser state are outside that buffer accounting.
func DecodeQOI(ctx context.Context, h QOIHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	c := 3
	switch h.Mode {
	case "RGB":
	case "RGBA":
		c = 4
	default:
		return empty, ErrQOIDecode
	}
	if h.DataOffset < 0 || h.DataOffset > len(data) {
		return empty, ErrQOIDecode
	}
	size, err := rasterBytes(h.Width, h.Height, c)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) {
		return empty, ErrWorkingLimit
	}
	out := make([]byte, int(size))
	var cache [64][4]byte
	previous := [4]byte{0, 0, 0, 255}
	cache[53] = previous
	pos := h.DataOffset
	for written := 0; written < len(out); {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		if pos == len(data) {
			return empty, ErrQOIDecode
		}
		op := data[pos]
		pos++
		value := previous
		run := 1
		switch {
		case op == 254:
			if len(data)-pos < 3 {
				return empty, ErrQOIDecode
			}
			copy(value[:3], data[pos:pos+3])
			pos += 3
		case op == 255:
			if len(data)-pos < 4 {
				return empty, ErrQOIDecode
			}
			copy(value[:], data[pos:pos+4])
			pos += 4
		case op>>6 == 0:
			value = cache[op&63]
		case op>>6 == 1:
			value[0] += ((op >> 4) & 3) - 2
			value[1] += ((op >> 2) & 3) - 2
			value[2] += (op & 3) - 2
		case op>>6 == 2:
			if pos == len(data) {
				return empty, ErrQOIDecode
			}
			second := data[pos]
			pos++
			green := (op & 63) - 32
			value[0] += green + (second >> 4) - 8
			value[1] += green
			value[2] += green + (second & 15) - 8
		default:
			run = int(op&63) + 1
		}
		if op < 192 || op >= 254 {
			previous = value
			hash := (int(value[0])*3 + int(value[1])*5 + int(value[2])*7 + int(value[3])*11) % 64
			cache[hash] = value
		}
		for i := 0; i < run && written < len(out); i++ {
			copy(out[written:written+c], value[:c])
			written += c
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, h.Mode, out}, nil
}
