package loginimage

import (
	"context"
	"encoding/binary"
)

// readPNGTail follows Pillow's load_end, preserving its distinction between
// the active chunk's permissive read and subsequent chunks' exact safe_read.
// Pixel mode/size and animation status were copied at Image.open; later IHDR
// and acTL mutations affect parser state, not the current image's identity.
func readPNGTail(ctx context.Context, data []byte, state pngHeaderState, stream PNGStreamImage, maxBytes int) (pngHeaderState, error) {
	empty := pngHeaderState{}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	if stream.Position < 0 || stream.Position > len(data) || stream.Remaining < 0 {
		return empty, ErrPNGHeader
	}
	c := pngTailCursor{ctx: ctx, data: data, position: stream.Position, remaining: maxBytes}
	if err := c.skip(stream.Remaining); err != nil {
		return empty, err
	}
	animated := state.header.Frames > 1
	state.sequence = stream.Sequence
	for {
		if err := c.skip(4); err != nil {
			return empty, err
		}
		header, err := c.take(min(8, len(data)-c.position))
		if err != nil {
			return empty, err
		}
		if len(header) < 8 {
			return state, nil
		}
		valid := true
		for _, v := range header[4:] {
			if !(v >= 'A' && v <= 'Z' || v >= 'a' && v <= 'z' || v >= '0' && v <= '9' || v == '_') {
				valid = false
				break
			}
		}
		if !valid {
			return state, nil
		}
		kind := string(header[4:])
		length := int(binary.BigEndian.Uint32(header))
		if kind == "IEND" || kind == "fcTL" && animated {
			return state, nil
		}
		if kind == "fdAT" {
			if length < 4 {
				return empty, ErrPNGHeader
			}
			seq, err := c.take(4)
			if err != nil {
				return empty, err
			}
			n := int64(binary.BigEndian.Uint32(seq))
			if state.sequence < 0 || n != state.sequence+1 {
				return empty, ErrPNGHeader
			}
			state.sequence = n
			length -= 4
		}
		body, err := c.take(length)
		if err != nil {
			return empty, err
		}
		if kind == "IDAT" || kind == "fdAT" {
			if !state.hasBBox && state.hasFrames {
				state.header.DefaultImage = true
			}
		} else if err := state.chunk(ctx, kind, body); err != nil {
			return empty, err
		}
	}
}

type pngTailCursor struct {
	ctx                 context.Context
	data                []byte
	position, remaining int
}

func (c *pngTailCursor) take(n int) ([]byte, error) {
	if err := c.ctx.Err(); err != nil {
		return nil, err
	}
	if n < 0 || n > len(c.data)-c.position {
		return nil, ErrPNGHeader
	}
	if n > c.remaining {
		return nil, ErrWorkingLimit
	}
	out := c.data[c.position : c.position+n]
	c.position += n
	c.remaining -= n
	return out, nil
}
func (c *pngTailCursor) skip(n int) error {
	if n < 0 {
		return ErrPNGHeader
	}
	_, err := c.take(min(n, len(c.data)-c.position))
	return err
}
