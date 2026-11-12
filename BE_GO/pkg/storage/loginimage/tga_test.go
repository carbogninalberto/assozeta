package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func tgaTestFile(w, h, kind, depth, flags int, pixels []byte) []byte {
	b := make([]byte, 18)
	b[2] = byte(kind)
	binary.LittleEndian.PutUint16(b[12:], uint16(w))
	binary.LittleEndian.PutUint16(b[14:], uint16(h))
	b[16] = byte(depth)
	b[17] = byte(flags)
	return append(b, pixels...)
}
func TestTGAInvertedAlphaAndOrientation(t *testing.T) {
	data := tgaTestFile(2, 1, 2, 16, 32, []byte{0, 124, 0, 252})
	h, err := ReadTGAHeader(context.Background(), data, 18)
	if err != nil || h.Mode != "RGBA" || h.RawMode != "BGRA;15Z" || !h.TopDown || h.FlipHorizontal || h.DataOffset != 18 {
		t.Fatal(h, err)
	}
	got, err := DecodeTGA(context.Background(), h, data, int64(len(data)+8))
	if err != nil || !bytes.Equal(got.Pixels, []byte{255, 0, 0, 255, 255, 0, 0, 0}) {
		t.Fatal(got, err)
	}
	h.FlipHorizontal = true
	got, err = DecodeTGA(context.Background(), h, data, 1024)
	if err != nil || !bytes.Equal(got.Pixels, []byte{255, 0, 0, 0, 255, 0, 0, 255}) {
		t.Fatal("horizontal flip", got, err)
	}
	if got, err := DecodeTGA(context.Background(), h, data, int64(len(data)+7)); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("pixel budget", err)
	}
}
func TestTGARLEPacketBoundaries(t *testing.T) {
	for _, tc := range []struct{ payload, pixels []byte }{
		{[]byte{3, 10, 20, 30, 40}, []byte{10, 20, 30, 40}},
		{[]byte{0x83, 10}, nil},
		{[]byte{5, 10, 20, 30, 40}, nil},
		{[]byte{5, 10, 20, 30, 40, 50, 60}, []byte{10, 20, 30, 40}},
	} {
		data := tgaTestFile(2, 2, 11, 8, 32, tc.payload)
		h, err := ReadTGAHeader(context.Background(), data, 18)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeTGA(context.Background(), h, data, 1024)
		if tc.pixels == nil {
			if !errors.Is(err, ErrTGADecode) || got.Pixels != nil {
				t.Fatal("packet accepted", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, tc.pixels) {
			t.Fatal(got, err)
		}
	}
}
func TestTGAPaletteChangesGrayCoreMode(t *testing.T) {
	data := tgaTestFile(1, 1, 3, 8, 32, []byte{0, 0, 0, 42})
	data[1] = 1
	data[5] = 1
	data[7] = 24
	h, err := ReadTGAHeader(context.Background(), data, 21)
	if err != nil || h.Mode != "L" || !h.HasPalette {
		t.Fatal(h, err)
	}
	got, err := DecodeTGA(context.Background(), h, data, 1024)
	if err != nil || got.Mode != "P" || !bytes.Equal(got.Pixels, []byte{42}) {
		t.Fatal("palette core mode", got, err)
	}
}

func TestTGAHeaderBudgetAndCancellation(t *testing.T) {
	data := tgaTestFile(1, 1, 3, 8, 32, []byte{1, 2, 3, 4, 5, 6, 42})
	data[0] = 3
	data[1] = 1
	data[5] = 1
	data[7] = 24
	for _, limit := range []int{0, 17, 20, 23, 24, 25} {
		h, err := ReadTGAHeader(context.Background(), data, limit)
		if limit < 24 {
			if !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
				t.Fatal("header budget", limit, err)
			}
		} else if err != nil || h.DataOffset != 24 {
			t.Fatal("exact header budget", h, err)
		}
	}
	for _, trigger := range []int{1, 2, 3, 4, 5} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if h, err := ReadTGAHeader(ctx, data, 1024); !errors.Is(err, context.Canceled) || h.Width != 0 {
			t.Fatal("header cancellation", trigger, err)
		}
		cancel()
	}
}
func TestTGAPixelCancellationOwnershipAndValidation(t *testing.T) {
	for _, input := range [][]byte{
		tgaTestFile(256, 256, 3, 8, 32, bytes.Repeat([]byte{42}, 256*256)),
		tgaTestFile(256, 256, 11, 8, 32, bytes.Repeat([]byte{255, 42}, 512)),
	} {
		h, err := ReadTGAHeader(context.Background(), input, 18)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeTGA(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("pixel cancellation", trigger, err)
			}
			cancel()
		}
	}
	input := tgaTestFile(1, 1, 2, 24, 32, []byte{10, 20, 30})
	before := bytes.Clone(input)
	h, err := ReadTGAHeader(context.Background(), input, 18)
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodeTGA(context.Background(), h, input, int64(len(input)+3))
	if err != nil {
		t.Fatal(err)
	}
	got.Pixels[0] = 99
	if !bytes.Equal(input, before) {
		t.Fatal("output aliases input")
	}
	for _, field := range []string{"mode", "raw", "depth", "palette", "offset", "dimensions"} {
		x := h
		switch field {
		case "mode":
			x.Mode = "L"
		case "raw":
			x.RawMode = "bad"
		case "depth":
			x.Depth = 8
		case "palette":
			x.HasPalette = true
		case "offset":
			x.DataOffset = -1
		case "dimensions":
			x.Width = 0
		}
		if got, err := DecodeTGA(context.Background(), x, input, 1024); err == nil || got.Pixels != nil {
			t.Fatal("invalid spec", field, err)
		}
	}
	maximum := int(^uint(0) >> 1)
	h = TGAHeader{Width: maximum - 1, Height: 1, Depth: 1, Mode: "1", RawMode: "1"}
	if got, err := DecodeTGA(context.Background(), h, []byte{0}, int64(maximum)); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("packed row overflow", err)
	}
}
