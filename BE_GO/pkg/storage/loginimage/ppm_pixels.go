package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
	"math"
)

var ErrPPMDecode = errors.New("login PPM pixel decoding failed")

const ppmBlockSize = 1 << 20

// DecodePPM returns normalized samples: mode 1 uses 0/255, I and F use four
// little-endian bytes. The budget includes input, output and the plain decoder's
// bounded block buffer plus ten bytes for a split token. It is not an RSS limit.
func DecodePPM(ctx context.Context, h PPMHeader, data []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if h.Mode == "" || h.Mode != ppmMode(h.Magic, h.MaxValue) || h.DataOffset < 0 || h.DataOffset > len(data) {
		return empty, ErrPPMDecode
	}
	if h.Mode == "F" {
		if h.Scale == 0 || math.IsNaN(h.Scale) || math.IsInf(h.Scale, 0) {
			return empty, ErrPPMDecode
		}
	} else if h.Mode != "1" && (h.MaxValue <= 0 || h.MaxValue >= 65536) {
		return empty, ErrPPMDecode
	}
	c := channels(h.Mode)
	if h.Mode == "P" {
		c = 1
	}
	if h.Mode == "I" || h.Mode == "F" {
		c = 4
	}
	size, err := rasterBytes(h.Width, h.Height, c)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || size > maxBytes-int64(len(data)) || size > int64(^uint(0)>>1) {
		return empty, ErrWorkingLimit
	}
	if h.DataOffset == len(data) {
		return empty, ErrPPMDecode
	}
	plain := h.Magic == "P1" || h.Magic == "P2" || h.Magic == "P3"
	if plain {
		scratchSize := min(ppmBlockSize, len(data)-h.DataOffset) + 10
		if int64(scratchSize) > maxBytes-int64(len(data))-size {
			return empty, ErrWorkingLimit
		}
		out := make([]byte, int(size))
		scratch := make([]byte, 0, scratchSize)
		if err := decodePPMPlain(ctx, h, data, out, scratch); err != nil {
			return empty, err
		}
		return Raster{h.Width, h.Height, h.Mode, out}, nil
	}
	source := data[h.DataOffset:]
	if h.Mode == "1" {
		if h.Width > int(^uint(0)>>1)-7 {
			return empty, ErrWorkingLimit
		}
		row := (int64(h.Width) + 7) / 8
		if row > int64(len(source))/int64(h.Height) {
			return empty, ErrPPMDecode
		}
		out := make([]byte, int(size))
		for y := 0; y < h.Height; y++ {
			for x := 0; x < h.Width; x++ {
				if x&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return empty, err
					}
				}
				out[y*h.Width+x] = (1 - ((source[int64(y)*row+int64(x/8)] >> uint(7-x%8)) & 1)) * 255
			}
		}
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return Raster{h.Width, h.Height, h.Mode, out}, nil
	}
	bands, unit := c, 1
	if h.Mode == "I" {
		bands = 1
	}
	if h.MaxValue > 255 {
		unit = 2
	}
	if h.Mode == "F" {
		bands = 1
		unit = 4
	}
	required, err := rasterBytes(h.Width, h.Height, bands*unit)
	if err != nil {
		return empty, err
	}
	if required > int64(len(source)) {
		return empty, ErrPPMDecode
	}
	out := make([]byte, int(size))
	samples := int(size)
	if h.Mode == "I" || h.Mode == "F" {
		samples /= 4
	}
	for i := 0; i < samples; i++ {
		if i&4095 == 0 {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
		}
		if h.Mode == "F" {
			y, x := i/h.Width, i%h.Width
			off := ((h.Height-1-y)*h.Width + x) * 4
			v := binary.LittleEndian.Uint32(source[off:])
			if h.Scale > 0 {
				v = binary.BigEndian.Uint32(source[off:])
			}
			binary.LittleEndian.PutUint32(out[i*4:], v)
			continue
		}
		v := int(source[i*unit])
		if unit == 2 {
			v = int(binary.BigEndian.Uint16(source[i*2:]))
		}
		outMax := 255
		if h.Mode == "I" {
			outMax = 65535
		}
		value := min(outMax, int(math.RoundToEven(float64(v)/float64(h.MaxValue)*float64(outMax))))
		if h.Mode == "I" {
			binary.LittleEndian.PutUint32(out[i*4:], uint32(value))
		} else {
			out[i] = byte(value)
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, h.Mode, out}, nil
}

// Plain decoding preserves Pillow's 1 MiB file reads. Comment removal can join
// tokens across lines/blocks. A final partial token is validated before any
// complete tokens in that block, even when those tokens fill the image.
func decodePPMPlain(ctx context.Context, h PPMHeader, data, out, scratch []byte) error {
	r := ppmPlainReader{ctx: ctx, data: data, pos: h.DataOffset}
	var half [10]byte
	halfLen, written := 0, 0
	for written < len(out) {
		if err := ctx.Err(); err != nil {
			return err
		}
		block := r.read()
		if len(block) == 0 {
			if halfLen == 0 {
				break
			}
			block = []byte{' '}
		}
		scratch = scratch[:0]
		scratch = append(scratch, half[:halfLen]...)
		var err error
		scratch, err = r.clean(block, scratch)
		if err != nil {
			return err
		}
		halfLen = 0
		if h.Mode == "1" {
			for i, c := range scratch {
				if i&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return err
					}
				}
				if ppmSpace(c) {
					continue
				}
				if c != '0' && c != '1' {
					return ErrPPMDecode
				}
				if written < len(out) {
					out[written] = (1 - (c - '0')) * 255
					written++
				}
			}
			continue
		}
		if len(scratch) > 0 && !ppmSpace(scratch[len(scratch)-1]) {
			start := len(scratch) - 1
			for start > 0 && !ppmSpace(scratch[start-1]) {
				if start&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return err
					}
				}
				start--
			}
			halfLen = len(scratch) - start
			if halfLen > 10 {
				return ErrPPMDecode
			}
			copy(half[:], scratch[start:])
			scratch = scratch[:start]
		}
		for i := 0; i < len(scratch) && written < len(out); {
			if i&4095 == 0 || written&4095 == 0 {
				if err := ctx.Err(); err != nil {
					return err
				}
			}
			if ppmSpace(scratch[i]) {
				i++
				continue
			}
			start := i
			for i < len(scratch) && !ppmSpace(scratch[i]) {
				i++
				if i-start > 10 {
					return ErrPPMDecode
				}
			}
			v, ok := ppmInteger(string(scratch[start:i]))
			if !ok || v < 0 || v > int64(h.MaxValue) {
				return ErrPPMDecode
			}
			outMax := 255
			if h.Mode == "I" {
				outMax = 65535
			}
			value := uint32(math.RoundToEven(float64(v) / float64(h.MaxValue) * float64(outMax)))
			if h.Mode == "I" {
				binary.LittleEndian.PutUint32(out[written:], value)
				written += 4
			} else {
				out[written] = byte(value)
				written++
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if written != len(out) {
		return ErrPPMDecode
	}
	return nil
}

type ppmPlainReader struct {
	ctx     context.Context
	data    []byte
	pos     int
	comment bool
}

func (r *ppmPlainReader) read() []byte {
	end := r.pos + min(ppmBlockSize, len(r.data)-r.pos)
	b := r.data[r.pos:end]
	r.pos = end
	return b
}
func (r *ppmPlainReader) clean(block, out []byte) ([]byte, error) {
	if r.comment {
		for len(block) > 0 {
			i := 0
			for i < len(block) && block[i] != '\r' && block[i] != '\n' {
				if i&4095 == 0 {
					if err := r.ctx.Err(); err != nil {
						return nil, err
					}
				}
				i++
			}
			if i < len(block) {
				// Pillow's _find_comment_end picks max(a,b) when one index
				// is zero, so a later opposite newline wins in this case.
				if i == 0 {
					for j := 1; j < len(block); j++ {
						if j&4095 == 0 {
							if err := r.ctx.Err(); err != nil {
								return nil, err
							}
						}
						if (block[j] == '\r' || block[j] == '\n') && block[j] != block[0] {
							i = j
							break
						}
					}
				}
				block = block[i+1:]
				break
			}
			block = r.read()
		}
	}
	r.comment = false
	for i := 0; i < len(block); i++ {
		if i&4095 == 0 {
			if err := r.ctx.Err(); err != nil {
				return nil, err
			}
		}
		if block[i] != '#' {
			out = append(out, block[i])
			continue
		}
		for i < len(block) && block[i] != '\r' && block[i] != '\n' {
			if i&4095 == 0 {
				if err := r.ctx.Err(); err != nil {
					return nil, err
				}
			}
			i++
		}
		if i == len(block) {
			r.comment = true
		}
	}
	if err := r.ctx.Err(); err != nil {
		return nil, err
	}
	return out, nil
}
