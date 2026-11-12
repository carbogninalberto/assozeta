package loginimage

import (
	"bytes"
	"context"
	"math"
	"strconv"
)

func validateBilevel(ctx context.Context, pixels []byte) error {
	for i, value := range pixels {
		if i&16383 == 0 {
			if err := ctx.Err(); err != nil {
				return err
			}
		}
		if value != 0 && value != 255 {
			return ErrRaster
		}
	}
	return ctx.Err()
}

// nearestBilevel follows Pillow 11.1's ImagingScaleAffine. Resize forces this
// path for mode 1, even when the caller supplies Lanczos. Its float32 box and
// accumulated double coordinates matter: 2 -> 7 keeps the central pixel black
// for input [black, white]. See LICENSE.pillow and README.md for provenance.
// Transform has already reserved source/output bytes and validated dimensions.
func nearestBilevel(ctx context.Context, input Raster, width, height int, remaining int64) (Raster, error) {
	empty := Raster{}
	identity := width == input.Width && height == input.Height
	const indexBytes = int64(strconv.IntSize / 8)
	if !identity && (int64(width) > math.MaxInt64/indexBytes || int64(width)*indexBytes > remaining) {
		return empty, ErrWorkingLimit
	}
	if err := validateBilevel(ctx, input.Pixels); err != nil {
		return empty, err
	}
	if identity {
		output := bytes.Clone(input.Pixels)
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return Raster{width, height, "1", output}, nil
	}
	xIndices := make([]int, width)
	xStep := float64(float32(input.Width)) / float64(width)
	xPosition := xStep * 0.5
	for x := range xIndices {
		if x&4095 == 0 {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
		}
		xIndices[x] = -1
		if xPosition >= 0 && xPosition < float64(input.Width) {
			xIndices[x] = int(xPosition)
		}
		xPosition += xStep
	}
	output := make([]byte, width*height)
	yStep := float64(float32(input.Height)) / float64(height)
	yPosition := yStep * 0.5
	for y := 0; y < height; y++ {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		if yPosition >= 0 && yPosition < float64(input.Height) {
			sourceRow := int(yPosition) * input.Width
			for x, sourceX := range xIndices {
				if x&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return empty, err
					}
				}
				if sourceX >= 0 {
					output[y*width+x] = input.Pixels[sourceRow+sourceX]
				}
			}
		}
		yPosition += yStep
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{width, height, "1", output}, nil
}
