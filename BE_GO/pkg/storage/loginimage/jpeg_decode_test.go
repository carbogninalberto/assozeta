package loginimage

import (
	"context"
	"errors"
	"os"
	"testing"
)

func TestJPEGDecoderRequiresBounds(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	limits := jpegTestLimits()
	limits.MaxRasterBytes = int64(^uint(0) >> 1)
	if decoder, err := NewJPEGDecoder(path, limits); decoder != nil || !errors.Is(err, ErrJPEGLimits) {
		t.Fatal("unrepresentable frame bound accepted", err)
	}
	limits = jpegTestLimits()
	limits.MaxEncodedBytes = 0
	if decoder, err := NewJPEGDecoder(path, limits); decoder != nil || !errors.Is(err, ErrJPEGLimits) {
		t.Fatal("unbounded input accepted", err)
	}
	limits = jpegTestLimits()
	limits.MaxEncodedBytes = 2
	decoder, err := NewJPEGDecoder(path, limits)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		input []byte
		want  error
	}{{nil, ErrJPEGDecode}, {[]byte{1, 2, 3}, ErrWorkingLimit}} {
		got, err := decoder.Decode(context.Background(), tc.input)
		if got.Pixels != nil || !errors.Is(err, tc.want) {
			t.Fatal("input validation failed", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := decoder.Decode(ctx, []byte{1}); got.Pixels != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation lost", err)
	}
}
func TestJPEGDecodedFrameValidation(t *testing.T) {
	good := []byte{'A', 'Z', 'J', 'P', 0, 0, 0, 2, 0, 0, 0, 1, 1, 0, 0, 0, 255, 255, 255, 255, 23, 42}
	got, err := decodeJPEGFrame(good, 2)
	if err != nil || got.Width != 2 || got.Height != 1 || got.Mode != "L" || len(got.Pixels) != 2 || got.Pixels[1] != 42 {
		t.Fatal("valid frame rejected", err)
	}
	for _, frame := range [][]byte{nil, good[:20], append(append([]byte(nil), good...), 0), append([]byte{'X'}, good[1:]...)} {
		if got, err := decodeJPEGFrame(frame, 2); got.Pixels != nil || !errors.Is(err, ErrJPEGDecode) {
			t.Fatal("bad frame accepted", err)
		}
	}
	for _, offset := range []int{4, 8, 12, 13, 14, 15} {
		bad := append([]byte(nil), good...)
		bad[offset] = 255
		if got, err := decodeJPEGFrame(bad, 2); got.Pixels != nil || !errors.Is(err, ErrJPEGDecode) {
			t.Fatal("invalid metadata accepted", offset, err)
		}
	}
	if got, err := decodeJPEGFrame(good, 1); got.Pixels != nil || !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("raster budget exceeded", err)
	}
}

func TestJPEGDecodedCommentFraming(t *testing.T) {
	frame := []byte{'A', 'Z', 'J', 'P', 0, 0, 0, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 42}
	got, err := decodeJPEGFrame(frame, 1)
	if err != nil || got.Comment == nil || len(got.Comment) != 0 || got.Pixels[0] != 42 {
		t.Fatal("empty comment differs from absent comment", err)
	}
	frame[19] = 1
	frame = append(frame[:20], 255, 42)
	got, err = decodeJPEGFrame(frame, 1)
	if err != nil || len(got.Comment) != 1 || got.Comment[0] != 255 || got.Pixels[0] != 42 {
		t.Fatal("binary comment lost", err)
	}
	for _, bad := range [][]byte{frame[:20], append(append([]byte(nil), frame[:16]...), 0, 1, 0, 0, 42)} {
		if got, err := decodeJPEGFrame(bad, 1); got.Pixels != nil || !errors.Is(err, ErrJPEGDecode) {
			t.Fatal("invalid comment framing accepted", err)
		}
	}
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	encoder, err := NewJPEGEncoder(path, jpegTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if data, err := encoder.EncodeWithComment(context.Background(), Raster{1, 1, "L", []byte{0}}, make([]byte, 65534)); data != nil || !errors.Is(err, ErrJPEGEncode) {
		t.Fatal("oversized JPEG marker accepted", err)
	}
}
