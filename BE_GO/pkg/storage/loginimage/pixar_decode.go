package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
)

var ErrPIXARHeader = errors.New("login PIXAR header invalid")

// ErrPIXARPixelLimit terminates Pillow registry lookup; other header errors
// reject this candidate and allow later formats to open the same data.
var ErrPIXARPixelLimit = fmt.Errorf("%w: pixel limit exceeded", ErrPIXARHeader)
var ErrPIXARDecode = errors.New("login PIXAR pixel decoding failed")

type PIXARHeader struct {
	Width, Height int
	PixelWarning  bool
}

func pixarSignature(b []byte) bool { return len(b) >= 4 && string(b[:4]) == "\x80\xe8\x00\x00" }

// ReadPIXARHeader follows Pillow 11.1's 512-byte read and RGB-only (14,2) mode.
// A short read is valid if all required words through offset 427 are present.
func ReadPIXARHeader(ctx context.Context, b []byte, maxBytes int) (PIXARHeader, error) {
	empty := PIXARHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 || min(4, len(b)) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if !pixarSignature(b) {
		return empty, ErrPIXARHeader
	}
	if min(512, len(b)) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if len(b) < 428 || binary.LittleEndian.Uint16(b[424:]) != 14 || binary.LittleEndian.Uint16(b[426:]) != 2 {
		return empty, ErrPIXARHeader
	}
	h := int(binary.LittleEndian.Uint16(b[416:]))
	w := int(binary.LittleEndian.Uint16(b[418:]))
	if w == 0 || h == 0 {
		return empty, ErrPIXARHeader
	}
	n := int64(w) * int64(h)
	if n > 178956970 {
		return empty, ErrPIXARPixelLimit
	}
	return PIXARHeader{w, h, n > 89478485}, nil
}

// DecodePIXAR copies packed RGB rows starting at byte 1024. Its logical budget
// includes the input and independently owned raster; it does not bound total RSS.
func DecodePIXAR(ctx context.Context, h PIXARHeader, b []byte, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if h.Width < 1 || h.Width > 65535 || h.Height < 1 || h.Height > 65535 || int64(h.Width)*int64(h.Height) > 178956970 {
		return empty, ErrPIXARDecode
	}
	n := int64(h.Width) * int64(h.Height) * 3
	if maxBytes <= 0 || int64(len(b)) > maxBytes || n > maxBytes-int64(len(b)) || n > int64(^uint(0)>>1) {
		return empty, ErrWorkingLimit
	}
	if len(b) < 1024 || n > int64(len(b)-1024) {
		return empty, ErrPIXARDecode
	}
	out := make([]byte, int(n))
	for pos := 0; pos < len(out); {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		next := pos + min(4096, len(out)-pos)
		copy(out[pos:next], b[1024+pos:1024+next])
		pos = next
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, "RGB", out}, nil
}
