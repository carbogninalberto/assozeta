package loginimage

import (
	"context"
	"errors"
)

var ErrGBRDecode = errors.New("login GBR pixel decoding failed")

// DecodeGBR copies the first raw L/RGBA image with no row alignment. The logical
// budget includes input plus independently owned output; it is not a total RSS cap.
func DecodeGBR(ctx context.Context, h GBRHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if (h.Version != 1 && h.Version != 2) || (h.Depth != 1 && h.Depth != 4) || (h.Depth == 1 && h.Mode != "L") || (h.Depth == 4 && h.Mode != "RGBA") {
		return empty, ErrGBRDecode
	}
	fixed := 20
	if h.Version == 2 {
		fixed = 28
	}
	if h.DataOffset < fixed || h.DataOffset > len(data) {
		return empty, ErrGBRDecode
	}
	size, err := rasterBytes(h.Width, h.Height, h.Depth)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) {
		return empty, ErrWorkingLimit
	}
	if size > int64(len(data)-h.DataOffset) {
		return empty, ErrGBRDecode
	}
	out := make([]byte, int(size))
	for pos := 0; pos < len(out); {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		n := min(4096, len(out)-pos)
		copy(out[pos:pos+n], data[h.DataOffset+pos:h.DataOffset+pos+n])
		pos += n
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, h.Mode, out}, nil
}
