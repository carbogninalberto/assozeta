package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrSGIHeader = errors.New("login SGI header invalid")

type SGIHeader struct {
	Width, Height, Channels, BPC, Dimension, Compression, DataOffset, HeaderBytes int
	Mode, RawMode                                                                 string
	PixelWarning                                                                  bool
}

func sgiSignature(data []byte) bool { return len(data) >= 2 && binary.BigEndian.Uint16(data) == 474 }

// ReadSGIHeader preserves Pillow's partial 512-byte header read and lazy
// admission of unknown compression values. Pixels always start at offset 512.
func ReadSGIHeader(ctx context.Context, data []byte, maxBytes int) (SGIHeader, error) {
	empty := SGIHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	n := min(512, len(data))
	if maxBytes <= 0 || n > maxBytes {
		return empty, ErrWorkingLimit
	}
	if n < 12 || !sgiSignature(data) {
		return empty, ErrSGIHeader
	}
	be := binary.BigEndian
	h := SGIHeader{Width: int(be.Uint16(data[6:])), Height: int(be.Uint16(data[8:])), Channels: int(be.Uint16(data[10:])), BPC: int(data[3]), Dimension: int(be.Uint16(data[4:])), Compression: int(data[2]), DataOffset: 512, HeaderBytes: n}
	if h.BPC != 1 && h.BPC != 2 {
		return empty, ErrSGIHeader
	}
	switch {
	case h.Channels == 1 && (h.Dimension == 1 || h.Dimension == 2):
		h.Mode = "L"
	case h.Channels == 3 && h.Dimension == 3:
		h.Mode = "RGB"
	case h.Channels == 4 && h.Dimension == 3:
		h.Mode = "RGBA"
	default:
		return empty, ErrSGIHeader
	}
	pixels := uint64(h.Width) * uint64(h.Height)
	if h.Width == 0 || h.Height == 0 || pixels > 178956970 {
		return empty, ErrSGIHeader
	}
	h.PixelWarning = pixels > 89478485
	h.RawMode = h.Mode
	if h.BPC == 2 {
		h.RawMode += ";16B"
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return h, nil
}
