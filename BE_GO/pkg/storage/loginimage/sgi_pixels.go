package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrSGIDecode = errors.New("login SGI pixel decoding failed")

// DecodeSGI preserves bottom-up planar samples and the reference RLE row-state
// semantics. Only the high byte of 16-bit samples reaches the normalized raster.
// Logical budgets count input, output and (for RLE) one normalized row; tables
// are read directly from the input. This does not bound aggregate RSS.
func DecodeSGI(ctx context.Context, h SGIHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	c := 0
	switch h.Mode {
	case "L":
		c = 1
	case "RGB":
		c = 3
	case "RGBA":
		c = 4
	default:
		return empty, ErrSGIDecode
	}
	raw := h.Mode
	if h.BPC == 2 {
		raw += ";16B"
	}
	if h.BPC != 1 && h.BPC != 2 || h.Channels != c || h.RawMode != raw || h.DataOffset != 512 || h.Compression < 0 || h.Compression > 1 || c == 1 && h.Dimension != 1 && h.Dimension != 2 || c != 1 && h.Dimension != 3 {
		return empty, ErrSGIDecode
	}
	size, err := rasterBytes(h.Width, h.Height, c)
	if err != nil {
		return empty, err
	}
	rowSize := int64(0)
	if h.Compression == 1 {
		rowSize, err = rasterBytes(h.Width, 1, c)
		if err != nil {
			return empty, err
		}
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) || rowSize > maxBytes-int64(len(data))-size {
		return empty, ErrWorkingLimit
	}
	if len(data) < 512 {
		return empty, ErrSGIDecode
	}
	if h.Compression == 0 {
		if size > int64(len(data)-512)/int64(h.BPC) {
			return empty, ErrSGIDecode
		}
		out := make([]byte, int(size))
		pos := 512
		for band := 0; band < c; band++ {
			for y := h.Height - 1; y >= 0; y-- {
				for x := 0; x < h.Width; x++ {
					if x&4095 == 0 {
						if err := ctx.Err(); err != nil {
							return empty, err
						}
					}
					out[(y*h.Width+x)*c+band] = data[pos]
					pos += h.BPC
				}
			}
		}
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return Raster{h.Width, h.Height, h.Mode, out}, nil
	}
	tableSize, err := rasterBytes(h.Height, 1, c*8)
	if err != nil {
		return empty, err
	}
	if tableSize > int64(len(data)-512) {
		return empty, ErrSGIDecode
	}
	out := make([]byte, int(size))
	row := make([]byte, int(rowSize))
	result := func() (Raster, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return Raster{h.Width, h.Height, h.Mode, out}, nil
	}
	be := binary.BigEndian
	for sourceY := 0; sourceY < h.Height; sourceY++ {
		for band := 0; band < c; band++ {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			entry := sourceY + band*h.Height
			pos := int64(be.Uint32(data[512+entry*4:]))
			// The reference passes its uint32 length table through a C int.
			packets := int32(be.Uint32(data[512+int(tableSize)/2+entry*4:]))
			if pos < 512 {
				return empty, ErrSGIDecode
			}
			x := 0
			for ; packets > 0; packets-- {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
				if pos > int64(len(data)-h.BPC) {
					return empty, ErrSGIDecode
				}
				op := data[int(pos)+h.BPC-1]
				pos += int64(h.BPC)
				// A nonzero last declared packet exits successfully before the
				// current row is stored. Remaining output retains its zeros.
				if packets == 1 && op != 0 {
					return result()
				}
				count := int(op & 127)
				if count == 0 {
					break
				}
				if count > h.Width-x {
					return empty, ErrSGIDecode
				}
				if op&128 != 0 {
					// expandrow's end pointer is the final byte, not one-past-end.
					if int64(count*h.BPC) > int64(len(data)-1)-pos {
						return empty, ErrSGIDecode
					}
					for i := 0; i < count; i++ {
						row[(x+i)*c+band] = data[int(pos)+i*h.BPC]
					}
					pos += int64(count * h.BPC)
				} else {
					need := 1
					if h.BPC == 2 {
						need = 3
					}
					if pos > int64(len(data)-need) {
						return empty, ErrSGIDecode
					}
					for i := 0; i < count; i++ {
						row[(x+i)*c+band] = data[int(pos)]
					}
					pos += int64(h.BPC)
				}
				x += count
			}
		}
		// Row storage persists across source rows, including unwritten bands.
		dst := (h.Height - 1 - sourceY) * int(rowSize)
		for at := 0; at < len(row); {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			n := min(4096, len(row)-at)
			copy(out[dst+at:dst+at+n], row[at:at+n])
			at += n
		}
	}
	return result()
}
