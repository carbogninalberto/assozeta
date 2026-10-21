package loginimage

import (
	"bytes"
	"context"
	"errors"
	"testing"
)

func jpegMarker(code byte, payload []byte) []byte {
	return append([]byte{255, code, byte((len(payload) + 2) >> 8), byte(len(payload) + 2)}, payload...)
}
func jpegHeaderOnly(w, h int, c byte) []byte {
	frame := []byte{8, byte(h >> 8), byte(h), byte(w >> 8), byte(w), c}
	for i := byte(0); i < c; i++ {
		frame = append(frame, i+1, 17, 0)
	}
	out := append([]byte{255, 216}, jpegMarker(192, frame)...)
	return append(out, jpegMarker(218, nil)...)
}
func TestJPEGMarkerHeaderIsLazy(t *testing.T) {
	input := jpegHeaderOnly(200, 100, 3)
	got, err := ReadJPEGMarkerHeader(context.Background(), input, len(input))
	if err != nil || got.Width != 200 || got.Height != 100 || got.Mode != "RGB" || got.HeaderBytes != len(input) {
		t.Fatal("header without pixel data rejected", got, err)
	}
	// Pixel codecs require tables and entropy data. Header recognition does not.
	input = append(input, bytes.Repeat([]byte{0}, 1<<20)...)
	got, err = ReadJPEGMarkerHeader(context.Background(), input, got.HeaderBytes)
	if err != nil || got.Width != 200 {
		t.Fatal("body incorrectly consumed by marker reader", err)
	}
	for _, mode := range []struct {
		c    byte
		name string
	}{{1, "L"}, {3, "RGB"}, {4, "CMYK"}} {
		header, err := ReadJPEGMarkerHeader(context.Background(), jpegHeaderOnly(17, 13, mode.c), 1024)
		if err != nil || header.Mode != mode.name {
			t.Fatal("wrong header mode", err)
		}
	}
}
func TestJPEGMarkerHeaderBoundsAndFailure(t *testing.T) {
	valid := jpegHeaderOnly(200, 100, 3)
	for _, input := range [][]byte{nil, valid[:2], valid[:len(valid)-1], jpegHeaderOnly(0, 100, 3), jpegHeaderOnly(1, 1, 2), jpegHeaderOnly(65535, 65535, 1)} {
		if got, err := ReadJPEGMarkerHeader(context.Background(), input, 1024); !errors.Is(err, ErrJPEGHeader) || got.Width != 0 {
			t.Fatal("invalid marker header accepted", err)
		}
	}
	if got, err := ReadJPEGMarkerHeader(context.Background(), valid, len(valid)-1); got.Width != 0 || !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("header byte budget lost", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := ReadJPEGMarkerHeader(ctx, valid, 1024); got.Width != 0 || !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled header returned", err)
	}
}
func TestJPEGMarkerHeaderCommentAndMetadataOwnership(t *testing.T) {
	source := jpegHeaderOnly(10, 20, 3)
	prefix := append(jpegMarker(254, []byte("first")), jpegMarker(254, []byte{0, 255})...)
	prefix = append(prefix, jpegMarker(225, []byte("Exif\x00\x00synthetic"))...)
	prefix = append(prefix, jpegMarker(226, []byte("MPF\x00synthetic"))...)
	input := append(append(append([]byte(nil), source[:2]...), prefix...), source[2:]...)
	before := append([]byte(nil), input...)
	got, err := ReadJPEGMarkerHeader(context.Background(), input, len(input))
	if err != nil || !bytes.Equal(got.Comment, []byte{0, 255}) || !bytes.Equal(got.EXIF, []byte("Exif\x00\x00synthetic")) || !bytes.Equal(got.MPF, []byte("synthetic")) {
		t.Fatal("metadata not preserved", err)
	}
	got.Comment[0] = 23
	got.EXIF[0] = 23
	got.MPF[0] = 23
	if !bytes.Equal(before, input) {
		t.Fatal("metadata aliases input")
	}
}

func TestJPEGMarkerCancellationDuringJunkReturnsNoPartialHeader(t *testing.T) {
	prefix := append([]byte{255, 216, 255, 216}, bytes.Repeat([]byte{'x'}, 100000)...)
	input := append(prefix, jpegHeaderOnly(200, 100, 3)[2:]...)
	for _, trigger := range []int{2, 4, 50, 10000} {
		base, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: base, cancel: cancel, trigger: trigger}
		got, err := ReadJPEGMarkerHeader(ctx, input, len(input))
		cancel()
		if got.Width != 0 || got.Comment != nil || !errors.Is(err, context.Canceled) {
			t.Fatal("mid-header cancellation lost", err)
		}
	}
}
func TestJPEGMarkerMetadataOrderingAndOffsets(t *testing.T) {
	prefix := jpegMarker(225, []byte("Exif\x00\x00first"))
	prefix = append(prefix, jpegMarker(225, []byte("Exif\x00\x00second"))...)
	prefix = append(prefix, jpegMarker(226, []byte("MPF\x00old"))...)
	prefix = append(prefix, jpegMarker(225, []byte(" hdrgm:Version=\"1.0\""))...)
	prefix = append(prefix, jpegMarker(226, []byte("MPF\x00"))...)
	input := append(append([]byte{255, 216}, prefix...), jpegHeaderOnly(200, 100, 3)[2:]...)
	got, err := ReadJPEGMarkerHeader(context.Background(), input, len(input))
	if err != nil || !bytes.Equal(got.EXIF, []byte("Exif\x00\x00firstsecond")) || got.MPF == nil || len(got.MPF) != 0 || !got.UltraHDR || got.MPFOffset != bytes.LastIndex(input, []byte("MPF\x00"))+4 {
		t.Fatal("metadata ordering/offset differs", got, err)
	}
}
