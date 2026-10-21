package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrGBRHeader = errors.New("login GBR header invalid")

type GBRHeader struct {
	Width, Height, Depth, Version                        int
	HeaderBytes, DataOffset, CommentOffset, CommentBytes int
	Mode                                                 string
	PixelWarning                                         bool
}

func gbrSignature(data []byte) bool {
	if len(data) < 8 || binary.BigEndian.Uint32(data) < 20 {
		return false
	}
	version := binary.BigEndian.Uint32(data[4:])
	return version == 1 || version == 2
}

// ReadGBRHeader records spans in the caller's input, without copying comments.
// Version-two sizes below 28 read the entire remaining input, matching Python's
// negative read length. The last byte read is discarded regardless of its value.
func ReadGBRHeader(ctx context.Context, data []byte, maxBytes int) (GBRHeader, error) {
	empty := GBRHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 || min(20, len(data)) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if len(data) < 20 || !gbrSignature(data) {
		return empty, ErrGBRHeader
	}
	read := func(pos int) uint64 { return uint64(binary.BigEndian.Uint32(data[pos:])) }
	declared, version, w, h, depth := read(0), read(4), read(8), read(12), read(16)
	if w == 0 || h == 0 || (depth != 1 && depth != 4) {
		return empty, ErrGBRHeader
	}
	fixed := 20
	if version == 2 {
		fixed = 28
		if min(fixed, len(data)) > maxBytes {
			return empty, ErrWorkingLimit
		}
		if len(data) < fixed || string(data[20:24]) != "GIMP" {
			return empty, ErrGBRHeader
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	n := len(data) - fixed
	if declared >= uint64(fixed) {
		n = int(min(uint64(n), declared-uint64(fixed)))
	}
	end := fixed + n
	if end > maxBytes {
		return empty, ErrWorkingLimit
	}
	if w > 178956970/h {
		return empty, ErrGBRHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	mode := "L"
	if depth == 4 {
		mode = "RGBA"
	}
	return GBRHeader{Width: int(w), Height: int(h), Depth: int(depth), Version: int(version), HeaderBytes: end, DataOffset: end, CommentOffset: fixed, CommentBytes: max(0, n-1), Mode: mode, PixelWarning: w*h > 89478485}, nil
}
