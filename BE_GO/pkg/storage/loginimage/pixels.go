// Package loginimage contains the pixel and data-URI stages of Assozeta's login
// image transform. File decoding and runtime wiring are separate.
package loginimage

import (
	"context"
	"errors"
	"math"
)

var ErrDimensions = errors.New("invalid login image dimensions")
var ErrRaster = errors.New("invalid login image pixel layout")
var ErrUnsupportedMode = errors.New("unsupported login image pixel mode")
var ErrWorkingLimit = errors.New("login image working limit exceeded")

type Plan struct {
	Width, Height int
	KeepOriginal  bool
}
type Raster struct {
	Width, Height int
	Mode          string
	Pixels        []byte
}

// Decide preserves compress_base64's strict disk-size threshold. A small image
// can be enlarged when its encoded data is over 1 MiB; under that limit only the
// JPEG representation changes. Zero-sized resize results are Django fallbacks.
func Decide(width, height, encodedBytes int) (Plan, error) {
	if width <= 0 || height <= 0 || encodedBytes < 0 {
		return Plan{}, ErrDimensions
	}
	ratio := math.Min(200/float64(width), 200/float64(height))
	w, h := int(float64(width)*ratio), int(float64(height)*ratio)
	if encodedBytes <= 1024*1024 {
		return Plan{width, height, w == width && h == height}, nil
	}
	if w <= 0 || h <= 0 {
		return Plan{}, ErrDimensions
	}
	return Plan{Width: w, Height: h}, nil
}
func channels(mode string) int {
	switch mode {
	case "1", "L":
		return 1
	case "RGB", "YCbCr":
		return 3
	case "RGBA", "CMYK", "RGBX":
		return 4
	}
	return 0
}
func rasterBytes(w, h, c int) (int64, error) {
	if w <= 0 || h <= 0 || c <= 0 || int64(w) > math.MaxInt64/int64(h)/int64(c) {
		return 0, ErrDimensions
	}
	return int64(w) * int64(h) * int64(c), nil
}

// Transform flattens RGBA on white, then applies Pillow's two-pass 8-bit Lanczos
// filter. Mode 1 uses nearest-neighbour sampling and normalized 0/255 bytes.
// maxWorkingBytes bounds logical input, pixel buffers and coefficients;
// process RSS/allocator overhead and global concurrent admission are separate.
// No failure returns pixels, and output never aliases the supplied input.
func Transform(ctx context.Context, input Raster, width, height int, maxWorkingBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	c := channels(input.Mode)
	if c == 0 {
		return empty, ErrUnsupportedMode
	}
	sourceBytes, err := rasterBytes(input.Width, input.Height, c)
	if err != nil {
		return empty, err
	}
	if sourceBytes != int64(len(input.Pixels)) {
		return empty, ErrRaster
	}
	outputMode := input.Mode
	outputC := c
	if input.Mode == "RGBA" {
		outputMode = "RGB"
		outputC = 3
	}
	targetBytes, err := rasterBytes(width, height, outputC)
	if err != nil {
		return empty, err
	}
	used := int64(0)
	reserve := func(n int64) bool {
		if n < 0 || n > maxWorkingBytes-used {
			return false
		}
		used += n
		return true
	}
	if maxWorkingBytes <= 0 || !reserve(sourceBytes) || !reserve(targetBytes) {
		return empty, ErrWorkingLimit
	}
	if input.Mode == "1" {
		return nearestBilevel(ctx, input, width, height, maxWorkingBytes-used)
	}
	if input.Mode == "RGBA" {
		n, _ := rasterBytes(input.Width, input.Height, 3)
		if !reserve(n) {
			return empty, ErrWorkingLimit
		}
	}
	if width != input.Width && height != input.Height {
		n, e := rasterBytes(width, input.Height, outputC)
		if e != nil || !reserve(n) {
			return empty, ErrWorkingLimit
		}
	}
	for _, axis := range [][2]int{{input.Width, width}, {input.Height, height}} {
		if axis[0] == axis[1] {
			continue
		}
		window := kernelWindow(axis[0], axis[1])
		if int64(axis[1]) > math.MaxInt64/32 || int64(axis[1]) > math.MaxInt64/int64(window)/4 || int64(window) > math.MaxInt64/8 {
			return empty, ErrWorkingLimit
		}
		// A single scratch array is reused for all output coefficients. Account for
		// slice headers and bounds as well as fixed-point weights.
		if !reserve(int64(axis[1])*32) || !reserve(int64(axis[1])*int64(window)*4) || !reserve(int64(window)*8) {
			return empty, ErrWorkingLimit
		}
	}
	prepared := input.Pixels
	if input.Mode == "RGBA" {
		prepared = make([]byte, input.Width*input.Height*3)
		for i, j := 0, 0; i < len(input.Pixels); i, j = i+4, j+3 {
			if i&16383 == 0 {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
			}
			a := int(input.Pixels[i+3])
			for ch := 0; ch < 3; ch++ {
				prepared[j+ch] = byte((int(input.Pixels[i+ch])*a + 255*(255-a) + 127) / 255)
			}
		}
	}
	working := prepared
	if width != input.Width {
		kernel, err := coefficients(ctx, input.Width, width)
		if err != nil {
			return empty, err
		}
		working = make([]byte, width*input.Height*outputC)
		if err := horizontal(ctx, prepared, working, input.Width, input.Height, width, outputC, kernel); err != nil {
			return empty, err
		}
	}
	if height != input.Height {
		kernel, err := coefficients(ctx, input.Height, height)
		if err != nil {
			return empty, err
		}
		output := make([]byte, width*height*outputC)
		if err := vertical(ctx, working, output, width, height, outputC, kernel); err != nil {
			return empty, err
		}
		working = output
	} else if width == input.Width {
		working = append([]byte(nil), prepared...)
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{width, height, outputMode, working}, nil
}

// The following fixed-point Lanczos procedure is derived from Pillow 11.1.0
// src/libImaging/Resample.c. See LICENSE.pillow and README.md for provenance.
const precision = 22

type kernelRow struct {
	start   int
	weights []int32
}

func kernelWindow(input, output int) int {
	scale := math.Max(float64(float32(input))/float64(output), 1)
	size := 2*math.Ceil(3*scale) + 1
	if size >= float64(input) {
		return input
	}
	return int(size)
}
func coefficients(ctx context.Context, input, output int) ([]kernelRow, error) {
	scale := float64(float32(input)) / float64(output)
	filterScale := math.Max(scale, 1)
	support := 3 * filterScale
	scratch := make([]float64, kernelWindow(input, output))
	rows := make([]kernelRow, output)
	for x := 0; x < output; x++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		center := (float64(x) + 0.5) * scale
		first := max(0, int(center-support+0.5))
		last := min(input, int(center+support+0.5))
		count := last - first
		sum := 0.0
		for j := 0; j < count; j++ {
			if j&4095 == 0 {
				if err := ctx.Err(); err != nil {
					return nil, err
				}
			}
			v := (float64(j+first) - center + 0.5) * (1.0 / filterScale)
			w := 0.0
			if v >= -3 && v < 3 {
				w = sinc(v) * sinc(v/3)
			}
			scratch[j] = w
			sum += w
		}
		weights := make([]int32, count)
		for j := range weights {
			v := scratch[j]
			if sum != 0 {
				v /= sum
			}
			if v < 0 {
				weights[j] = int32(v*(1<<precision) - 0.5)
			} else {
				weights[j] = int32(v*(1<<precision) + 0.5)
			}
		}
		rows[x] = kernelRow{first, weights}
	}
	return rows, nil
}
func sinc(value float64) float64 {
	if value == 0 {
		return 1
	}
	v := value * math.Pi
	return math.Sin(v) / v
}
func clip(value int64) byte {
	value >>= precision
	if value < 0 {
		return 0
	}
	if value > 255 {
		return 255
	}
	return byte(value)
}
func horizontal(ctx context.Context, source, output []byte, inW, inH, outW, c int, kernel []kernelRow) error {
	for y := 0; y < inH; y++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		for x, row := range kernel {
			for ch := 0; ch < c; ch++ {
				total := int64(1 << (precision - 1))
				for j, w := range row.weights {
					if j&4095 == 0 {
						if err := ctx.Err(); err != nil {
							return err
						}
					}
					total += int64(source[(y*inW+row.start+j)*c+ch]) * int64(w)
				}
				output[(y*outW+x)*c+ch] = clip(total)
			}
		}
	}
	return nil
}
func vertical(ctx context.Context, source, output []byte, width, height, c int, kernel []kernelRow) error {
	for y := 0; y < height; y++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		row := kernel[y]
		for x := 0; x < width; x++ {
			for ch := 0; ch < c; ch++ {
				total := int64(1 << (precision - 1))
				for j, w := range row.weights {
					if j&4095 == 0 {
						if err := ctx.Err(); err != nil {
							return err
						}
					}
					total += int64(source[((row.start+j)*width+x)*c+ch]) * int64(w)
				}
				output[(y*width+x)*c+ch] = clip(total)
			}
		}
	}
	return nil
}
