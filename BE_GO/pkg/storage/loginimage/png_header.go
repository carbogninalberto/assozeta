package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"hash/crc32"
)

var ErrPNGHeader = errors.New("invalid login PNG header")

type PNGRect struct{ X, Y, Width, Height int }

// PNGHeader describes Pillow's lazy first-frame admission. It stops at the
// first recognized IDAT/fdAT or IEND, without requiring any compressed pixels.
// Tile can be smaller than the canvas. InvalidTile preserves text metadata that
// is accepted by Image.open but cannot describe a decodable rectangle.
type PNGHeader struct {
	PNGPixelSpec
	Mode                                string
	HeaderBytes, DataOffset, DataLength int
	DataKind                            string
	Tile                                PNGRect
	InvalidTile                         bool
	Frames                              int
	DefaultImage, PixelWarning          bool
	Comment                             []byte
}

type pngHeaderState struct {
	header     PNGHeader
	hasFrames  bool
	frameCount uint32
	hasBBox    bool
	sequence   int64
	disposal   int
	textMemory int64
}

func ReadPNGHeader(ctx context.Context, data []byte, maxHeaderBytes int) (PNGHeader, error) {
	state, err := readPNGHeaderState(ctx, data, maxHeaderBytes)
	return state.header, err
}

func readPNGHeaderState(ctx context.Context, data []byte, maxHeaderBytes int) (pngHeaderState, error) {
	empty := pngHeaderState{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxHeaderBytes < 8 {
		return empty, ErrWorkingLimit
	}
	if len(data) < 8 || !bytes.Equal(data[:8], []byte("\x89PNG\r\n\x1a\n")) {
		return empty, ErrPNGHeader
	}
	cursor := pngHeaderCursor{ctx: ctx, data: data, position: 8, limit: maxHeaderBytes}
	state := pngHeaderState{sequence: -1, disposal: -1}
	for {
		chunk, err := cursor.take(8)
		if err != nil {
			return empty, err
		}
		length := int(binary.BigEndian.Uint32(chunk))
		kind := string(chunk[4:])
		for _, c := range chunk[4:] {
			if !(c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '_') {
				return empty, ErrPNGHeader
			}
		}
		stop := kind == "IEND" || kind == "IDAT" && state.header.Mode != ""
		if kind == "fdAT" {
			if length < 4 {
				return empty, ErrPNGHeader
			}
			seq, err := cursor.take(4)
			if err != nil {
				return empty, err
			}
			n := int64(binary.BigEndian.Uint32(seq))
			if state.sequence < 0 || state.sequence != n-1 {
				return empty, ErrPNGHeader
			}
			state.sequence = n
			if state.header.Mode != "" {
				length -= 4
				stop = true
			}
		}
		if (kind == "IDAT" || kind == "fdAT") && state.header.Mode == "" && !state.hasBBox && state.hasFrames {
			// Pillow sets default_image before missing raw mode falls through
			// to its unknown-chunk reader. For fdAT that reader starts after
			// the sequence word but still consumes the original chunk length.
			state.header.DefaultImage = true
		}
		if stop {
			h := state.header
			if kind != "IEND" {
				if !state.hasBBox {
					h.Tile = PNGRect{Width: h.Width, Height: h.Height}
					if state.hasFrames {
						h.DefaultImage = true
					}
				}
			}
			h.HeaderBytes, h.DataOffset, h.DataLength, h.DataKind = cursor.position, cursor.position, length, kind
			h.Frames = 1
			if state.hasFrames {
				h.Frames = int(state.frameCount)
				if h.DefaultImage {
					h.Frames++
				}
				// Frame-zero disposal PREVIOUS becomes BACKGROUND and crops the extent.
				if state.hasBBox && h.InvalidTile && (state.disposal == 1 || state.disposal == 2) {
					return empty, ErrPNGHeader
				}
			}
			if kind == "IEND" {
				h.Tile = PNGRect{}
				h.InvalidTile = false
			}
			if h.Mode == "" || h.Width <= 0 || h.Height <= 0 || uint64(h.Width)*uint64(h.Height) > 178956970 {
				return empty, ErrPNGHeader
			}
			h.PixelWarning = uint64(h.Width)*uint64(h.Height) > 89478485
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			state.header = h
			return state, nil
		}
		body, err := cursor.take(length)
		if err != nil {
			return empty, err
		}
		if err := state.chunk(ctx, kind, body); err != nil {
			return empty, err
		}
		checksum, err := cursor.take(4)
		if err != nil {
			return empty, err
		}
		crc := crc32.ChecksumIEEE(chunk[4:])
		for i := 0; i < len(body); i += 65536 {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			crc = crc32.Update(crc, crc32.IEEETable, body[i:min(i+65536, len(body))])
		}
		if crc != binary.BigEndian.Uint32(checksum) {
			return empty, ErrPNGHeader
		}
	}
}

func (s *pngHeaderState) chunk(ctx context.Context, kind string, body []byte) error {
	h := &s.header
	switch kind {
	case "IHDR":
		if len(body) < 13 || body[11] != 0 {
			return ErrPNGHeader
		}
		h.Width, h.Height = int(binary.BigEndian.Uint32(body)), int(binary.BigEndian.Uint32(body[4:]))
		if mode, _, _ := pngPixelMode(int(body[8]), int(body[9])); mode != "" {
			h.Mode = mode
			h.Depth = int(body[8])
			h.Color = int(body[9])
		}
		if body[12] != 0 {
			h.Interlace = true
		}
	case "tRNS":
		if (h.Mode == "1" || h.Mode == "L" || h.Mode == "I;16") && len(body) < 2 || h.Mode == "RGB" && len(body) < 6 {
			return ErrPNGHeader
		}
	case "gAMA":
		if len(body) < 4 {
			return ErrPNGHeader
		}
	case "cHRM":
		if len(body)%4 != 0 {
			return ErrPNGHeader
		}
	case "sRGB":
		if len(body) < 1 {
			return ErrPNGHeader
		}
	case "pHYs":
		if len(body) < 9 {
			return ErrPNGHeader
		}
	case "acTL":
		if len(body) < 8 {
			return ErrPNGHeader
		}
		if s.hasFrames {
			s.hasFrames = false
			return nil
		}
		frames := binary.BigEndian.Uint32(body)
		if frames > 0 && frames <= 0x80000000 {
			s.hasFrames = true
			s.frameCount = frames
		}
	case "fcTL":
		if len(body) < 26 {
			return ErrPNGHeader
		}
		seq := int64(binary.BigEndian.Uint32(body))
		if s.sequence < 0 && seq != 0 || s.sequence >= 0 && s.sequence != seq-1 {
			return ErrPNGHeader
		}
		s.sequence = seq
		width, height := uint64(binary.BigEndian.Uint32(body[4:])), uint64(binary.BigEndian.Uint32(body[8:]))
		x, y := uint64(binary.BigEndian.Uint32(body[12:])), uint64(binary.BigEndian.Uint32(body[16:]))
		if x+width > uint64(h.Width) || y+height > uint64(h.Height) {
			return ErrPNGHeader
		}
		h.Tile = PNGRect{int(x), int(y), int(width), int(height)}
		h.InvalidTile = false
		s.hasBBox = true
		s.disposal = int(body[24])
	case "tEXt", "zTXt", "iTXt", "iCCP":
		return s.textChunk(ctx, kind, body)
	}
	return nil
}

type pngHeaderCursor struct {
	ctx             context.Context
	data            []byte
	position, limit int
}

func (c *pngHeaderCursor) take(n int) ([]byte, error) {
	if err := c.ctx.Err(); err != nil {
		return nil, err
	}
	if n < 0 || n > len(c.data)-c.position {
		return nil, ErrPNGHeader
	}
	if n > c.limit-c.position {
		return nil, ErrWorkingLimit
	}
	p := c.data[c.position : c.position+n]
	c.position += n
	return p, nil
}
