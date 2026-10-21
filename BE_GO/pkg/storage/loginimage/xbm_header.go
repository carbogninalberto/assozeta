package loginimage

import (
	"context"
	"errors"
	"regexp"
	"strconv"
)

var ErrXBMHeader = errors.New("login XBM header invalid")

type XBMHeader struct {
	Width, Height, DataOffset, HeaderBytes int
	Mode                                   string
	PixelWarning                           bool
}

// Hotspot declarations are optional and ignored by JPEG saving. The reference's
// all-byte suffix already accepts them, so no separate hotspot capture is needed.
// Explicit ASCII whitespace includes vertical tab, unlike Go regexp's \s.
var xbmHeaderPattern = regexp.MustCompile(`^[\x09-\x0d ]*#define[ \t]+[^\n]*_width[ \t]+([0-9]+)[\r\n]+#define[ \t]+[^\n]*_height[ \t]+([0-9]+)[\r\n]+(?s:.*)_bits\[\]`)

func xbmSignature(data []byte) bool {
	b := data[:min(16, len(data))]
	for len(b) > 0 && (b[0] == ' ' || b[0] >= 9 && b[0] <= 13) {
		b = b[1:]
	}
	return len(b) >= 7 && string(b[:7]) == "#define"
}

// ReadXBMHeader preserves Pillow's 16-byte recognition prefix and 512-byte
// greedy header window. The budget counts the entire consumed window, including
// any pixel text inside it, rather than just the selected tile offset.
func ReadXBMHeader(ctx context.Context, data []byte, maxBytes int) (XBMHeader, error) {
	empty := XBMHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	n := min(512, len(data))
	if maxBytes <= 0 || n > maxBytes {
		return empty, ErrWorkingLimit
	}
	if !xbmSignature(data) {
		return empty, ErrXBMHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	match := xbmHeaderPattern.FindSubmatchIndex(data[:n])
	if match == nil {
		return empty, ErrXBMHeader
	}
	w, err := strconv.ParseUint(string(data[match[2]:match[3]]), 10, 64)
	if err != nil {
		return empty, ErrXBMHeader
	}
	h, err := strconv.ParseUint(string(data[match[4]:match[5]]), 10, 64)
	if err != nil {
		return empty, ErrXBMHeader
	}
	if w == 0 || h == 0 || w > 178956970/h {
		return empty, ErrXBMHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return XBMHeader{int(w), int(h), match[1], n, "1", w*h > 89478485}, nil
}
