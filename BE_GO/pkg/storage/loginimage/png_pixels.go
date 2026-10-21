package loginimage

import (
	"bytes"
	"compress/zlib"
	"context"
	"errors"
	"io"
)

var ErrPNGDecode = errors.New("login PNG pixel decoding failed")

// PNGPixelSpec describes one PNG tile after file/header admission. Compressed
// bytes contain one zlib stream, without chunk framing or CRCs. Callers must
// handle chunk boundaries separately: concatenation can change Pillow errors.
// This primitive does not perform PNG metadata admission or APNG composition.
type PNGPixelSpec struct {
	Width, Height int
	Depth, Color  int
	Interlace     bool
}

// DecodePNGScanlines implements PNG filters, Adam7 passes and Pillow's pixel
// unpacking. Mode 1 is unpacked to one byte (0/255) per pixel; I;16 uses little
// endian samples. P and LA retain their modes, which cannot be saved as JPEG.
// maxWorkingBytes bounds input, output and row buffers, not zlib internals/RSS.
func DecodePNGScanlines(ctx context.Context, spec PNGPixelSpec, data []byte, maxWorkingBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxWorkingBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	mode, sourceChannels, outputChannels := pngPixelMode(spec.Depth, spec.Color)
	if mode == "" {
		return empty, ErrPNGDecode
	}
	size, err := rasterBytes(spec.Width, spec.Height, outputChannels)
	if err != nil {
		return empty, err
	}
	// Reserve full-width rows even for interlacing. Check arithmetic before any
	// allocation; encoded samples can require more bytes than normalized pixels.
	sourceSize, err := rasterBytes(spec.Width, 1, sourceChannels*spec.Depth)
	if err != nil || sourceSize > int64(^uint(0)>>1)-7 {
		return empty, ErrWorkingLimit
	}
	rowBytes := (sourceSize + 7) / 8
	used := int64(len(data))
	for _, n := range []int64{size, rowBytes + 1, rowBytes} {
		if n > maxWorkingBytes-used {
			return empty, ErrWorkingLimit
		}
		used += n
	}
	if used > int64(^uint(0)>>1) {
		return empty, ErrWorkingLimit
	}
	reader, err := zlib.NewReader(&pngContextReader{ctx: ctx, reader: bytes.NewReader(data)})
	if err != nil {
		if ctx.Err() != nil {
			return empty, ctx.Err()
		}
		return empty, ErrPNGDecode
	}
	defer reader.Close()
	output := make([]byte, int(size))
	current, previous := make([]byte, int(rowBytes)+1), make([]byte, int(rowBytes))
	passes := [][4]int{{0, 0, 1, 1}}
	if spec.Interlace {
		passes = [][4]int{{0, 0, 8, 8}, {4, 0, 8, 8}, {0, 4, 4, 8}, {2, 0, 4, 4}, {0, 2, 2, 4}, {1, 0, 2, 2}, {0, 1, 1, 2}}
	}
passesLoop:
	for _, pass := range passes {
		x0, y0, dx, dy := pass[0], pass[1], pass[2], pass[3]
		if x0 >= spec.Width || y0 >= spec.Height {
			continue
		}
		width := (spec.Width - x0 + dx - 1) / dx
		count := (width*sourceChannels*spec.Depth + 7) / 8
		clear(previous)
		for y := y0; y < spec.Height; y += dy {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			row := current[:count+1]
			ended, err := pngReadRow(reader, row)
			if err != nil {
				if ctx.Err() != nil {
					return empty, ctx.Err()
				}
				return empty, ErrPNGDecode
			}
			if row[0] > 4 {
				return empty, ErrPNGDecode
			}
			bpp := (sourceChannels*spec.Depth + 7) / 8
			for i := 1; i < len(row); i++ {
				if i&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return empty, err
					}
				}
				var a, c byte
				if i > bpp {
					a = row[i-bpp]
					c = previous[i-1-bpp]
				}
				b := previous[i-1]
				switch row[0] {
				case 1:
					row[i] += a
				case 2:
					row[i] += b
				case 3:
					row[i] += byte((int(a) + int(b)) / 2)
				case 4:
					row[i] += pngPaeth(a, b, c)
				}
			}
			for x := 0; x < width; x++ {
				if x&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return empty, err
					}
				}
				dst := output[(y*spec.Width+x0+x*dx)*outputChannels:]
				src := row[1:]
				switch {
				case spec.Depth < 8:
					value := (src[x*spec.Depth/8] >> uint(8-spec.Depth-x*spec.Depth%8)) & byte((1<<spec.Depth)-1)
					if spec.Color == 0 {
						value *= byte(255 / ((1 << spec.Depth) - 1))
					}
					dst[0] = value
				case spec.Depth == 8:
					copy(dst[:outputChannels], src[x*sourceChannels:][:sourceChannels])
				case spec.Color == 0:
					dst[0], dst[1] = src[x*2+1], src[x*2]
				case spec.Color == 4:
					v := src[x*4]
					dst[0], dst[1], dst[2], dst[3] = v, v, v, src[x*4+2]
				default:
					for c := 0; c < sourceChannels; c++ {
						dst[c] = src[(x*sourceChannels+c)*2]
					}
				}
			}
			if ended {
				// Pillow accepts Z_STREAM_END after a complete row and leaves
				// all pixels not yet visited (including Adam7 passes) zero.
				break passesLoop
			}
			copy(previous, row[1:])
		}
	}
	// Stop at the final required row. Pillow tolerates a missing trailer and
	// does not validate checksums beyond surplus inflated bytes. pngReadRow
	// still propagates a checksum error encountered while producing a row.
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{spec.Width, spec.Height, mode, output}, nil
}
func pngPixelMode(depth, color int) (string, int, int) {
	switch color {
	case 0:
		switch depth {
		case 1:
			return "1", 1, 1
		case 2, 4, 8:
			return "L", 1, 1
		case 16:
			return "I;16", 1, 2
		}
	case 2:
		if depth == 8 || depth == 16 {
			return "RGB", 3, 3
		}
	case 3:
		if depth == 1 || depth == 2 || depth == 4 || depth == 8 {
			return "P", 1, 1
		}
	case 4:
		if depth == 8 {
			return "LA", 2, 2
		}
		if depth == 16 {
			return "RGBA", 2, 4
		}
	case 6:
		if depth == 8 || depth == 16 {
			return "RGBA", 4, 4
		}
	}
	return "", 0, 0
}
func pngReadRow(reader io.Reader, row []byte) (ended bool, err error) {
	for len(row) > 0 {
		n, err := reader.Read(row)
		row = row[n:]
		if err != nil {
			if len(row) == 0 {
				if errors.Is(err, io.EOF) {
					return true, nil
				}
				if errors.Is(err, io.ErrUnexpectedEOF) {
					return false, nil
				}
			}
			return false, err
		}
		if n == 0 {
			return false, io.ErrNoProgress
		}
	}
	return false, nil
}
func pngPaeth(a, b, c byte) byte {
	p := int(a) + int(b) - int(c)
	pa, pb, pc := pngAbs(p-int(a)), pngAbs(p-int(b)), pngAbs(p-int(c))
	if pa <= pb && pa <= pc {
		return a
	}
	if pb <= pc {
		return b
	}
	return c
}
func pngAbs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

type pngContextReader struct {
	ctx    context.Context
	reader *bytes.Reader
}

func (r *pngContextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(p[:min(len(p), 4096)])
}
