package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"strconv"
	"time"
)

var ErrWebPDecode = errors.New("login WebP decoding failed")
var ErrWebPCodecUnavailable = errors.New("login WebP codec unavailable")
var ErrWebPLimits = errors.New("invalid login WebP limits")

type WebPLimits struct {
	MaxInputBytes   int
	MaxWorkingBytes int64
	Concurrency     int
	Timeout         time.Duration
}
type WebPHeader struct {
	Width, Height            int
	Mode                     string
	Frames, Loop, Background uint32
}
type WebPDecoder struct {
	process *jpegProcess
	limits  WebPLimits
}

func NewWebPDecoder(path string, l WebPLimits) (*WebPDecoder, error) {
	if l.MaxWorkingBytes <= 0 || l.MaxWorkingBytes > int64(^uint(0)>>1)-32 {
		return nil, ErrWebPLimits
	}
	p, err := newJPEGProcess(path, JPEGLimits{MaxRasterBytes: l.MaxWorkingBytes, MaxEncodedBytes: l.MaxInputBytes, Concurrency: l.Concurrency, Timeout: l.Timeout})
	if errors.Is(err, ErrJPEGLimits) {
		return nil, ErrWebPLimits
	}
	if errors.Is(err, ErrJPEGCodecUnavailable) {
		return nil, ErrWebPCodecUnavailable
	}
	if err != nil {
		return nil, err
	}
	return &WebPDecoder{p, l}, nil
}
func webpSignature(b []byte) bool {
	return len(b) >= 16 && string(b[:4]) == "RIFF" && string(b[8:12]) == "WEBP" && (string(b[12:16]) == "VP8 " || string(b[12:16]) == "VP8L" || string(b[12:16]) == "VP8X")
}
func (d *WebPDecoder) run(ctx context.Context, b []byte, pixels bool) (WebPHeader, []byte, error) {
	empty := WebPHeader{}
	if err := ctx.Err(); err != nil {
		return empty, nil, err
	}
	if len(b) > d.limits.MaxInputBytes {
		return empty, nil, ErrWorkingLimit
	}
	if !webpSignature(b) {
		return empty, nil, ErrWebPDecode
	}
	operation, output := "webp-info", 32
	if pixels {
		operation = "webp-decode"
		output += int(d.limits.MaxWorkingBytes)
	}
	frame, err := d.process.run(ctx, []string{operation, strconv.Itoa(len(b)), strconv.FormatInt(d.limits.MaxWorkingBytes, 10)}, bytes.NewReader(b), output, ErrWebPDecode)
	if errors.Is(err, ErrJPEGCodecUnavailable) {
		return empty, nil, ErrWebPCodecUnavailable
	}
	if err != nil {
		return empty, nil, err
	}
	return decodeWebPFrame(frame, d.limits.MaxWorkingBytes, pixels)
}
func (d *WebPDecoder) Inspect(ctx context.Context, b []byte) (WebPHeader, error) {
	h, _, err := d.run(ctx, b, false)
	return h, err
}
func (d *WebPDecoder) Decode(ctx context.Context, b []byte) (Raster, error) {
	h, pixels, err := d.run(ctx, b, true)
	if err != nil {
		return Raster{}, err
	}
	return Raster{h.Width, h.Height, h.Mode, pixels}, nil
}

func decodeWebPFrame(frame []byte, maxBytes int64, pixels bool) (WebPHeader, []byte, error) {
	empty := WebPHeader{}
	if len(frame) < 32 || string(frame[:4]) != "AZWP" || frame[13] != 0 || frame[14] != 0 || frame[15] != 0 || binary.BigEndian.Uint32(frame[28:]) != 0 {
		return empty, nil, ErrWebPDecode
	}
	w, h := binary.BigEndian.Uint32(frame[4:]), binary.BigEndian.Uint32(frame[8:])
	c := frame[12]
	if w == 0 || h == 0 || w > 16777216 || h > 16777216 || uint64(w)*uint64(h) > 178956970 || (c != 3 && c != 4) {
		return empty, nil, ErrWebPDecode
	}
	count := int64(w) * int64(h) * int64(c)
	mode := "RGB"
	if c == 4 {
		mode = "RGBA"
	}
	header := WebPHeader{int(w), int(h), mode, binary.BigEndian.Uint32(frame[16:]), binary.BigEndian.Uint32(frame[20:]), binary.BigEndian.Uint32(frame[24:])}
	if header.Frames == 0 || header.Loop > 65535 {
		return empty, nil, ErrWebPDecode
	}
	if pixels {
		if count > maxBytes {
			return empty, nil, ErrWorkingLimit
		}
		if count != int64(len(frame)-32) {
			return empty, nil, ErrWebPDecode
		}
		return header, frame[32:], nil
	}
	if len(frame) != 32 {
		return empty, nil, ErrWebPDecode
	}
	return header, nil, nil
}
