package loginimage

import "context"

// DecodeCUR preserves Pillow 13's masked grayscale cursor path. Both DIB halves
// are decoded in display order; the top AND plane masks the bottom XOR plane.
// The budget includes input, both decoded planes and the resulting LA raster.
func DecodeCUR(ctx context.Context, h BMPHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if h.CURMaskHeight == 0 {
		return DecodeBMP(ctx, h, data, maxBytes)
	}
	if h.Mode != "LA" || (h.RawMode != "1" && h.RawMode != "L") || h.CURMaskHeight < 2 || h.CURMaskHeight%2 != 0 || h.CURMaskHeight/2 != h.Height {
		return empty, ErrBMPDecode
	}
	size, err := rasterBytes(h.Width, h.Height, 2)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > (maxBytes-int64(len(data)))/2 {
		return empty, ErrWorkingLimit
	}
	source := h
	source.Mode = h.RawMode
	source.Height = h.CURMaskHeight
	source.CURMaskHeight = 0
	planes, err := DecodeBMP(ctx, source, data, maxBytes-size)
	if err != nil {
		return empty, err
	}
	out := make([]byte, int(size))
	n := len(out) / 2
	for i := 0; i < n; i++ {
		if i&4095 == 0 {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
		}
		alpha := 255 - int(planes.Pixels[i])
		out[2*i] = byte((int(planes.Pixels[n+i])*alpha + 127) / 255)
		out[2*i+1] = byte(alpha)
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, "LA", out}, nil
}
