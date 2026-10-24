package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrQOIHeader = errors.New("login QOI header invalid")

type QOIHeader struct {
	Width, Height, DataOffset, HeaderBytes int
	Mode                                   string
	PixelWarning                           bool
}

func qoiSignature(data []byte) bool { return len(data) >= 4 && string(data[:4]) == "qoif" }

// ReadQOIHeader follows Pillow 11.1's lazy admission: only channel value 3
// selects RGB, and the colorspace byte is skipped even when it is absent.
// The byte budget counts the 13 consumed bytes, not the seek to offset 14.
func ReadQOIHeader(ctx context.Context, data []byte, maxBytes int) (QOIHeader, error) {
	empty := QOIHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	pos := 0
	read := func(n int) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		n = min(n, len(data)-pos)
		if n > maxBytes-pos {
			return nil, ErrWorkingLimit
		}
		b := data[pos : pos+n]
		pos += n
		return b, nil
	}
	b, err := read(4)
	if err != nil {
		return empty, err
	}
	if !qoiSignature(b) {
		return empty, ErrQOIHeader
	}
	w, err := read(4)
	if err != nil {
		return empty, err
	}
	if len(w) != 4 {
		return empty, ErrQOIHeader
	}
	h, err := read(4)
	if err != nil {
		return empty, err
	}
	if len(h) != 4 {
		return empty, ErrQOIHeader
	}
	channels, err := read(1)
	if err != nil {
		return empty, err
	}
	if len(channels) != 1 {
		return empty, ErrQOIHeader
	}
	width, height := uint64(binary.BigEndian.Uint32(w)), uint64(binary.BigEndian.Uint32(h))
	if width == 0 || height == 0 || width*height > 178956970 {
		return empty, ErrQOIHeader
	}
	mode := "RGBA"
	if channels[0] == 3 {
		mode = "RGB"
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return QOIHeader{int(width), int(height), 14, pos, mode, width*height > 89478485}, nil
}
