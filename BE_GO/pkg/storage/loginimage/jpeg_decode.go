package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"strconv"
)

var ErrJPEGDecode = errors.New("login JPEG decoding failed")

// JPEGDecoder decodes normalized 8-bit pixels using the bundled native helper.
// It does not implement Pillow's lazy Image.open metadata/format recognition.
// Use one shared instance; decoder and encoder admission limits are independent.
type JPEGDecoder struct{ *jpegProcess }

// JPEGImage retains the last COM marker before the first scan, as Image.open
// does. Comments after the first scan do not replace this value.
type JPEGImage struct {
	Raster
	Comment []byte
}

const jpegFrameOverhead = 20 + 65533

func NewJPEGDecoder(path string, limits JPEGLimits) (*JPEGDecoder, error) {
	if limits.MaxRasterBytes > int64(^uint(0)>>1)-jpegFrameOverhead {
		return nil, ErrJPEGLimits
	}
	process, err := newJPEGProcess(path, limits)
	if err != nil {
		return nil, err
	}
	return &JPEGDecoder{process}, nil
}

func (d *JPEGDecoder) Decode(ctx context.Context, input []byte) (JPEGImage, error) {
	if err := ctx.Err(); err != nil {
		return JPEGImage{}, err
	}
	if len(input) > d.limits.MaxEncodedBytes {
		return JPEGImage{}, ErrWorkingLimit
	}
	if len(input) < 3 || input[0] != 0xff || input[1] != 0xd8 || input[2] != 0xff {
		return JPEGImage{}, ErrJPEGDecode
	}
	frame, err := d.run(ctx, []string{"decode", strconv.Itoa(len(input)), strconv.FormatInt(d.limits.MaxRasterBytes, 10)}, bytes.NewReader(input), int(d.limits.MaxRasterBytes)+jpegFrameOverhead, ErrJPEGDecode)
	if err != nil {
		return JPEGImage{}, err
	}
	return decodeJPEGFrame(frame, d.limits.MaxRasterBytes)
}

// The child publishes one complete AZJP frame only after successful decoding:
// magic, big-endian width/height, channel count, three zero bytes, big-endian
// comment length (0xffffffff means absent), comment and packed pixels.
func decodeJPEGFrame(frame []byte, maxBytes int64) (JPEGImage, error) {
	if len(frame) < 20 || string(frame[:4]) != "AZJP" || frame[13] != 0 || frame[14] != 0 || frame[15] != 0 {
		return JPEGImage{}, ErrJPEGDecode
	}
	width, height := binary.BigEndian.Uint32(frame[4:8]), binary.BigEndian.Uint32(frame[8:12])
	if width == 0 || height == 0 || width > 65500 || height > 65500 {
		return JPEGImage{}, ErrJPEGDecode
	}
	mode := ""
	switch frame[12] {
	case 1:
		mode = "L"
	case 3:
		mode = "RGB"
	case 4:
		mode = "CMYK"
	default:
		return JPEGImage{}, ErrJPEGDecode
	}
	size := int64(width) * int64(height) * int64(frame[12])
	if size > maxBytes {
		return JPEGImage{}, ErrWorkingLimit
	}
	length := binary.BigEndian.Uint32(frame[16:20])
	offset := 20
	var comment []byte
	if length != ^uint32(0) {
		if length > 65533 || int64(length) > int64(len(frame)-20) {
			return JPEGImage{}, ErrJPEGDecode
		}
		offset += int(length)
		comment = frame[20:offset]
	}
	if size != int64(len(frame)-offset) {
		return JPEGImage{}, ErrJPEGDecode
	}
	return JPEGImage{Raster: Raster{int(width), int(height), mode, frame[offset:]}, Comment: comment}, nil
}
