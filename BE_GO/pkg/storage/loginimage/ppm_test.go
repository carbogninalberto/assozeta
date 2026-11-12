package loginimage

import (
	"bytes"
	"context"
	"errors"
	"math"
	"testing"
)

func TestPPMHeaderAndPixels(t *testing.T) {
	data := []byte("P6\n# comment\n+3 1\n2\n\x00\x01\x02\x02\x01\x00\x03\xff\x01")
	h, err := ReadPPMHeader(context.Background(), data, len(data))
	if err != nil || h.Width != 3 || h.Height != 1 || h.Mode != "RGB" || h.MaxValue != 2 || h.DataOffset != 20 {
		t.Fatal(h, err)
	}
	got, err := DecodePPM(context.Background(), h, data, 1024)
	if err != nil || !bytes.Equal(got.Pixels, []byte{0, 128, 255, 255, 128, 0, 255, 255, 128}) {
		t.Fatal(got, err)
	}
	if got, err := ReadPPMHeader(context.Background(), data, 19); !errors.Is(err, ErrWorkingLimit) || got.Width != 0 {
		t.Fatal("header budget", err)
	}
	for _, limit := range []int64{int64(len(data) + 8), int64(len(data) + 9)} {
		got, err := DecodePPM(context.Background(), h, data, limit)
		if limit == int64(len(data)+8) {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("pixel budget", err)
			}
		} else if err != nil {
			t.Fatal("exact budget", err)
		}
	}
}

func TestPPMPlainBlockValidation(t *testing.T) {
	for _, tc := range []struct {
		input  string
		pixels []byte
	}{
		{"P1 1 1\n0x", nil}, // PBM validates the whole block, including surplus pixels.
		{"P2 1 1 255\n42 x ", []byte{42}},
		{"P2 1 1 255\n42 12345678901", nil}, // Trailing half-token checked first.
		{"P2 1 1 255\n4# comment\n2 ", []byte{42}},
		{"P2 1 1 255\n4# unterminated", []byte{4}},
	} {
		data := []byte(tc.input)
		h, err := ReadPPMHeader(context.Background(), data, 1024)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodePPM(context.Background(), h, data, 1<<20)
		if tc.pixels == nil {
			if !errors.Is(err, ErrPPMDecode) || got.Pixels != nil {
				t.Fatal(tc.input, err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, tc.pixels) {
			t.Fatal(tc.input, got, err)
		}
	}
}

func TestPPMRowArithmeticOverflow(t *testing.T) {
	maximum := int(^uint(0) >> 1)
	h := PPMHeader{Width: maximum - 1, Height: 1, Magic: "P4", Mode: "1"}
	got, err := DecodePPM(context.Background(), h, []byte{0}, int64(maximum))
	if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("row arithmetic overflow", err)
	}
}

func TestPPMPlainBudgetAndOwnership(t *testing.T) {
	data := []byte("P2 3 1 255\n0 127 255 ")
	before := bytes.Clone(data)
	h, err := ReadPPMHeader(context.Background(), data, len(data))
	if err != nil {
		t.Fatal(err)
	}
	exact := int64(len(data) + 3 + len(data) - h.DataOffset + 10)
	for _, delta := range []int64{-1, 0, 1} {
		got, err := DecodePPM(context.Background(), h, data, exact+delta)
		if delta < 0 {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("plain budget", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, []byte{0, 127, 255}) {
			t.Fatal("plain exact budget", got, err)
		} else {
			got.Pixels[0] = 99
		}
	}
	if !bytes.Equal(data, before) {
		t.Fatal("input aliases output")
	}
	for _, limit := range []int{h.DataOffset - 1, h.DataOffset, h.DataOffset + 1} {
		got, err := ReadPPMHeader(context.Background(), data, limit)
		if limit < h.DataOffset {
			if !errors.Is(err, ErrWorkingLimit) || got.Width != 0 {
				t.Fatal("exact header budget", err)
			}
		} else if err != nil || got != h {
			t.Fatal("header boundary", got, err)
		}
	}
}

func TestPPMCancellationAndValidation(t *testing.T) {
	header := append([]byte("P5 #"), bytes.Repeat([]byte{'x'}, 65536)...)
	header = append(header, []byte("\n1 1 255\n\x2a")...)
	for _, trigger := range []int{2, 10, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if got, err := ReadPPMHeader(ctx, header, len(header)); !errors.Is(err, context.Canceled) || got.Width != 0 {
			t.Fatal("header cancellation", err)
		}
		cancel()
	}
	for _, tc := range []struct {
		h    PPMHeader
		data []byte
	}{
		{PPMHeader{Width: 1024, Height: 512, Magic: "P5", Mode: "L", MaxValue: 255}, make([]byte, 1024*512)},
		{PPMHeader{Width: 1024, Height: 512, Magic: "P4", Mode: "1"}, make([]byte, 1024*512/8)},
		{PPMHeader{Width: 1024, Height: 512, Magic: "Pf", Mode: "F", Scale: -1}, make([]byte, 1024*512*4)},
		{PPMHeader{Width: 1024, Height: 512, Magic: "P2", Mode: "L", MaxValue: 255}, bytes.Repeat([]byte("42 "), 1024*512)},
		{PPMHeader{Width: 1024, Height: 512, Magic: "P1", Mode: "1"}, bytes.Repeat([]byte("01"), 1024*256)},
		{PPMHeader{Width: 1, Height: 1, Magic: "P2", Mode: "L", MaxValue: 255}, append(append([]byte("4#"), bytes.Repeat([]byte{'x'}, 2*ppmBlockSize)...), []byte("\n2 ")...)},
	} {
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodePPM(ctx, tc.h, tc.data, 8<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("pixel cancellation", tc.h.Magic, trigger, err)
			}
			cancel()
		}
	}
	for _, h := range []PPMHeader{
		{Width: 1, Height: 1, Magic: "P0", Mode: "L", MaxValue: 255},
		{Width: 1, Height: 1, Magic: "P5", Mode: "L", MaxValue: 0},
		{Width: 1, Height: 1, Magic: "P5", Mode: "RGB", MaxValue: 255},
		{Width: 1, Height: 1, Magic: "P5", Mode: "L", MaxValue: 255, DataOffset: -1},
		{Width: 1, Height: 1, Magic: "P5", Mode: "L", MaxValue: 255, DataOffset: 9},
		{Width: 1, Height: 1, Magic: "Pf", Mode: "F", Scale: math.NaN()},
		{Width: 1, Height: 1, Magic: "Pf", Mode: "F", Scale: math.Inf(1)},
		{Width: 1, Height: 1, Magic: "Pf", Mode: "F", Scale: 0},
	} {
		if got, err := DecodePPM(context.Background(), h, []byte{42}, 1024); !errors.Is(err, ErrPPMDecode) || got.Pixels != nil {
			t.Fatal("bad specification", h, err)
		}
	}
}
