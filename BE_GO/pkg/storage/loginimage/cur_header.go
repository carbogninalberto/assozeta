package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
)

var ErrCURHeader = errors.New("login CUR header invalid")

var ErrCURRejected = fmt.Errorf("%w: recoverable opening failure", ErrCURHeader)
var ErrCURTerminal = fmt.Errorf("%w: terminal opening failure", ErrCURHeader)

func curSignature(b []byte) bool { return bytes.HasPrefix(b, []byte{0, 0, 2, 0}) }

// ReadCURHeader follows Pillow's strictly increasing width AND height selection.
// Directory zeros are literal zeros; hotspots, color count and byte size are
// ignored. A zero DIB offset leaves the file cursor after the directory. Only a
// declared offset of 22 enables the raw 32-bit BGRA special case.
func ReadCURHeader(ctx context.Context, b []byte, maxBytes int) (BMPHeader, error) {
	empty := BMPHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 || min(len(b), 6) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if len(b) < 6 || !curSignature(b) {
		return empty, ErrCURRejected
	}
	n := int(binary.LittleEndian.Uint16(b[4:]))
	pos := 6
	var selected []byte
	for i := 0; i < n; i++ {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		end := min(pos+16, len(b))
		if end > maxBytes {
			return empty, ErrWorkingLimit
		}
		s := b[pos:end]
		pos = end
		if len(selected) == 0 {
			selected = s
			continue
		}
		if len(s) == 0 {
			return empty, ErrCURRejected
		}
		if s[0] > selected[0] {
			if len(s) < 2 || len(selected) < 2 {
				return empty, ErrCURRejected
			}
			if s[1] > selected[1] {
				selected = s
			}
		}
	}
	if len(selected) < 16 {
		return empty, ErrCURRejected
	}
	declared := binary.LittleEndian.Uint32(selected[12:])
	offset := uint64(declared)
	if offset == 0 {
		offset = uint64(pos)
	}
	if offset >= uint64(len(b)) {
		return empty, ErrCURRejected
	}
	h, err := readBMPHeader(ctx, b[int(offset):], maxBytes-pos, true)
	if err != nil {
		return empty, err
	}
	if declared == 22 && h.Bits == 32 && h.Compression == 0 {
		h.Mode = "RGBA"
		h.RawMode = "BGRA"
	}
	h.DataOffset += int(offset)
	h.HeaderBytes += pos
	h.Format = "CUR"
	return h, nil
}
