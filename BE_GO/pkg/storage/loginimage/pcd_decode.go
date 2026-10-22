package loginimage

import (
	"context"
	"errors"
)

var ErrPCDHeader = errors.New("login PCD header invalid")
var ErrPCDDecode = errors.New("login PCD pixel decoding failed")

type PCDHeader struct {
	Width, Height int
	Orientation   uint8
}

func pcdSignature(b []byte) bool { return len(b) >= 2052 && string(b[2048:2052]) == "PCD_" }
func ReadPCDHeader(ctx context.Context, b []byte, maxBytes int) (PCDHeader, error) {
	empty := PCDHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	if !pcdSignature(b) {
		return empty, ErrPCDHeader
	}
	n := min(1539, len(b)-2048)
	if n > maxBytes {
		return empty, ErrWorkingLimit
	}
	if n < 1539 {
		return empty, ErrPCDHeader
	}
	h := PCDHeader{768, 512, b[3586] & 3}
	if h.Orientation == 1 || h.Orientation == 3 {
		h.Width, h.Height = 512, 768
	}
	return h, nil
}

// DecodePCD converts the uncompressed 768x512 two-row PhotoYCC blocks. Pillow
// 13 rotates orientations 1/3 by 90/270 degrees. Write directly into the rotated
// output so the logical budget remains one input plus one RGB raster.
func DecodePCD(ctx context.Context, h PCDHeader, b []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	width, height := 768, 512
	if h.Orientation == 1 || h.Orientation == 3 {
		width, height = 512, 768
	}
	if h.Width != width || h.Height != height || h.Orientation > 3 {
		return empty, ErrPCDDecode
	}
	const offset = 96 * 2048
	const pixels = 768 * 512 * 3
	if maxBytes <= 0 || int64(len(b)) > maxBytes || pixels > maxBytes-int64(len(b)) {
		return empty, ErrWorkingLimit
	}
	if len(b) < offset+3*768*256 {
		return empty, ErrPCDDecode
	}
	out := make([]byte, pixels)
	clip := func(v int16) byte {
		if v < 0 {
			return 0
		}
		if v > 255 {
			return 255
		}
		return byte(v)
	}
	for y := 0; y < 512; y += 2 {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		block := b[offset+(y/2)*2304:]
		for row := 0; row < 2; row++ {
			for x := 0; x < 768; x++ {
				light := pcdL[block[row*768+x]]
				cb := block[1536+x/2]
				cr := block[1920+x/2]
				dx, dy := x, y+row
				switch h.Orientation {
				case 1:
					dx, dy = y+row, 767-x
				case 3:
					dx, dy = 511-y-row, x
				}
				pos := (dy*width + dx) * 3
				out[pos] = clip(light + pcdCR[cr])
				out[pos+1] = clip(light + pcdGR[cr] + pcdGB[cb])
				out[pos+2] = clip(light + pcdCB[cb])
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{width, height, "RGB", out}, nil
}
