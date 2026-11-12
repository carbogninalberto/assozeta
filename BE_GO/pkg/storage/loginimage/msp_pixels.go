package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrMSPDecode = errors.New("login MSP pixel decoding failed")

// DecodeMSP interprets packed MSB-first samples. LinS row records contribute to
// one continuous packed stream; their expanded lengths need not match a row.
// Every declared record is validated, including those beyond the final pixel.
// Expansion streams directly into output, avoiding an unbounded intermediate.
// Logical budgets count input plus output, not aggregate RSS.
func DecodeMSP(ctx context.Context, h MSPHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if h.Mode != "1" || len(data) < 32 {
		return empty, ErrMSPDecode
	}
	size, err := rasterBytes(h.Width, h.Height, 1)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > int64(^uint(0)>>1) || size > maxBytes-int64(len(data)) {
		return empty, ErrWorkingLimit
	}
	rowBytes := (int64(h.Width)-1)/8 + 1
	needed := rowBytes * int64(h.Height) // rowBytes <= width; rasterBytes checked that product.
	if h.RLE {
		if h.Height > (len(data)-32)/2 {
			return empty, ErrMSPDecode
		}
	} else if needed > int64(len(data)-32) {
		return empty, ErrMSPDecode
	}
	out := make([]byte, int(size))
	packed := int64(0)
	emit := func(value byte, count int64) error {
		count = min(count, needed-packed)
		for ; count > 0; count-- {
			if packed%512 == 0 {
				if err := ctx.Err(); err != nil {
					return err
				}
			}
			y, x := int(packed/rowBytes), int(packed%rowBytes)*8
			for bit := 0; bit < min(8, h.Width-x); bit++ {
				out[y*h.Width+x+bit] = ((value >> uint(7-bit)) & 1) * 255
			}
			packed++
		}
		return nil
	}
	if !h.RLE {
		for _, value := range data[32 : 32+int(needed)] {
			if err := emit(value, 1); err != nil {
				return empty, err
			}
		}
	} else {
		pos := 32 + h.Height*2
		for y := 0; y < h.Height; y++ {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			n := int(binary.LittleEndian.Uint16(data[32+y*2:]))
			if n > len(data)-pos {
				return empty, ErrMSPDecode
			}
			row := data[pos : pos+n]
			pos += n
			if n == 0 {
				if err := emit(255, rowBytes); err != nil {
					return empty, err
				}
				continue
			}
			for i := 0; i < len(row); {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
				count := int(row[i])
				i++
				if count == 0 {
					if len(row)-i < 2 {
						return empty, ErrMSPDecode
					}
					count, value := int64(row[i]), row[i+1]
					i += 2
					if err := emit(value, count); err != nil {
						return empty, err
					}
				} else {
					// Python's literal slice clips to the remaining row bytes, while its
					// cursor still advances by the declared count.
					for _, value := range row[i : i+min(count, len(row)-i)] {
						if err := emit(value, 1); err != nil {
							return empty, err
						}
					}
					i += count
				}
			}
		}
	}
	if packed != needed {
		return empty, ErrMSPDecode
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, "1", out}, nil
}
