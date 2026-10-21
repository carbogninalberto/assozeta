package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrMSPHeader = errors.New("login MSP header invalid")

type MSPHeader struct {
	Width, Height, HeaderBytes int
	Mode                       string
	RLE, PixelWarning          bool
}

func mspSignature(data []byte) bool {
	return len(data) >= 4 && (string(data[:4]) == "DanM" || string(data[:4]) == "LinS")
}

// ReadMSPHeader validates all sixteen checksum words before admitting raw DanM
// or compressed LinS images. Other header metadata does not affect JPEG output.
func ReadMSPHeader(ctx context.Context, data []byte, maxBytes int) (MSPHeader, error) {
	empty := MSPHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 || min(32, len(data)) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if len(data) < 32 || !mspSignature(data) {
		return empty, ErrMSPHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	var checksum uint16
	for i := 0; i < 32; i += 2 {
		checksum ^= binary.LittleEndian.Uint16(data[i:])
	}
	w, h := uint64(binary.LittleEndian.Uint16(data[4:])), uint64(binary.LittleEndian.Uint16(data[6:]))
	if checksum != 0 || w == 0 || h == 0 || w*h > 178956970 {
		return empty, ErrMSPHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return MSPHeader{Width: int(w), Height: int(h), HeaderBytes: 32, Mode: "1", RLE: string(data[:4]) == "LinS", PixelWarning: w*h > 89478485}, nil
}
