package loginimage

import (
	"context"
	"errors"
)

var ErrGIFDecode = errors.New("login GIF pixels invalid")

// The LZW dictionary and interlace policy follows Pillow 11.1 GifDecode.c:
// Copyright (c) Secret Labs AB 1997-99; Fredrik Lundh 1995-97.
// See LICENSE.pillow. Full subblocks must be present before consuming them;
// decoding completes at the last output pixel, without requiring an end code.
// Logical memory charges input, output and the fixed dictionary/expansion stack.
func DecodeGIF(ctx context.Context, h GIFHeader, input []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	n, err := rasterBytes(h.Width, h.Height, 1)
	if err != nil || h.Width > 131070 || h.Height > 131070 || h.Left < 0 || h.Top < 0 || h.Right <= h.Left || h.Bottom <= h.Top || h.Right > h.Width || h.Bottom > h.Height || h.DataOffset < 0 || h.DataOffset > len(input) || h.Bits < 0 || h.Bits > 12 || (h.Mode != "L" && h.Mode != "P") || h.Transparency < -1 || h.Transparency > 255 {
		return empty, ErrGIFDecode
	}
	const dictionaryBytes = 4096*2 + 4096 + 4096
	if maxBytes <= 0 || int64(len(input)) > maxBytes || n > maxBytes-int64(len(input))-dictionaryBytes {
		return empty, ErrWorkingLimit
	}
	out := make([]byte, int(n))
	if h.Transparency > 0 {
		for i := range out {
			if i%4096 == 0 {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
			}
			out[i] = byte(h.Transparency)
		}
	}
	var links [4096]uint16
	var values, stack [4096]byte
	pos, remaining := h.DataOffset, 0
	available := min(len(input), pos+65536)
	refill := func() error {
		if available == len(input) {
			return ErrGIFDecode
		}
		available = min(len(input), available+65536)
		return nil
	}
	var buffer uint32
	bits := 0
	nextByte := func() (byte, error) {
		for remaining == 0 {
			if err := ctx.Err(); err != nil {
				return 0, err
			}
			if pos >= available {
				if err := refill(); err != nil {
					return 0, err
				}
				continue
			}
			n := int(input[pos])
			if n+1 > available-pos {
				if err := refill(); err != nil {
					return 0, err
				}
				continue
			}
			remaining = n
			pos++
		}
		b := input[pos]
		pos++
		remaining--
		return b, nil
	}
	clear := 1 << h.Bits
	end := clear + 1
	next := clear + 2
	width := h.Bits + 1
	mask := (1 << width) - 1
	first := true
	lastCode, lastData := 0, byte(0)
	x, y, pass := 0, 0, 0
	tileW, tileH := h.Right-h.Left, h.Bottom-h.Top
	emit := func(value byte) bool {
		out[(h.Top+y)*h.Width+h.Left+x] = value
		x++
		if x == tileW {
			x = 0
			if !h.Interlace {
				y++
			} else {
				steps := [4]int{8, 8, 4, 2}
				starts := [4]int{0, 4, 2, 1}
				y += steps[pass]
				for y >= tileH && pass < 3 {
					pass++
					y = starts[pass]
				}
			}
		}
		return y >= tileH
	}
	success := func() (Raster, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return Raster{h.Width, h.Height, h.Mode, out}, nil
	}
	for {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		for bits < width {
			b, err := nextByte()
			if err != nil {
				return empty, err
			}
			buffer |= uint32(b) << bits
			bits += 8
		}
		code := int(buffer) & mask
		buffer >>= width
		bits -= width
		if code == clear {
			if !first {
				next = clear + 2
				width = h.Bits + 1
				mask = (1 << width) - 1
				first = true
			}
			continue
		}
		// Pillow returns to ImageFile on an end code, which reads another
		// 64 KiB before reentering the decoder, even with buffered bytes left.
		if code == end {
			if err := refill(); err != nil {
				return empty, err
			}
			continue
		}
		if first {
			if code > clear {
				return empty, ErrGIFDecode
			}
			lastCode = code
			lastData = byte(code)
			first = false
			if emit(lastData) {
				return success()
			}
			continue
		}
		current := code
		index := len(stack)
		if code > next {
			return empty, ErrGIFDecode
		}
		if code == next {
			index--
			stack[index] = lastData
			code = lastCode
		}
		for code >= clear {
			if index == 0 || code >= len(links) {
				return empty, ErrGIFDecode
			}
			index--
			stack[index] = values[code]
			code = int(links[code])
		}
		lastData = byte(code)
		if next < len(links) {
			values[next] = lastData
			links[next] = uint16(lastCode)
			if next == mask && width < 12 {
				width++
				mask = (1 << width) - 1
			}
			next++
		}
		lastCode = current
		if emit(lastData) {
			return success()
		}
		for ; index < len(stack); index++ {
			if emit(stack[index]) {
				return success()
			}
		}
	}
}
