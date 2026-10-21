package loginimage

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"testing"
)

func xbmTestFile(w, h int, payload []byte) []byte {
	return append([]byte(fmt.Sprintf("#define im_width %d\n#define im_height %d\nstatic char im_bits[]", w, h)), payload...)
}
func TestXBMHeaderGreedyMarkerAndBudget(t *testing.T) {
	data := xbmTestFile(8, 1, []byte("x80\n_bits[]x01"))
	h, err := ReadXBMHeader(context.Background(), data, len(data))
	if err != nil || h.Width != 8 || h.Height != 1 || h.Mode != "1" || h.DataOffset != len(data)-3 || h.HeaderBytes != len(data) {
		t.Fatal(h, err)
	}
	got, err := DecodeXBM(context.Background(), h, data, int64(len(data)+8))
	if err != nil || !bytes.Equal(got.Pixels, []byte{255, 0, 0, 0, 0, 0, 0, 0}) {
		t.Fatal(got, err)
	}
	if h, err := ReadXBMHeader(context.Background(), data, len(data)-1); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
		t.Fatal("header budget", err)
	}
	for _, n := range []int{9, 10} {
		input := append(bytes.Repeat([]byte{' '}, n), data...)
		h, err := ReadXBMHeader(context.Background(), input, 512)
		if n == 9 {
			if err != nil {
				t.Fatal(err)
			}
		} else if !errors.Is(err, ErrXBMHeader) || h.Width != 0 {
			t.Fatal("16 byte prefix", err)
		}
	}
}
func TestXBMHexAndRowPadding(t *testing.T) {
	for _, tc := range []struct {
		source string
		value  int
	}{
		{"x01", 1}, {"x80", 128}, {"xgF", 15}, {"x1,", 16}, {"xxf", 15}, {"xfx", 240}, {"Xff", -1}, {"x0", -1},
	} {
		data := xbmTestFile(8, 1, []byte(tc.source))
		h, err := ReadXBMHeader(context.Background(), data, 512)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeXBM(context.Background(), h, data, 1024)
		if tc.value < 0 {
			if !errors.Is(err, ErrXBMDecode) || got.Pixels != nil {
				t.Fatal(tc.source, err)
			}
		} else {
			expected := make([]byte, 8)
			for i := range expected {
				expected[i] = byte((tc.value>>i)&1) * 255
			}
			if err != nil || !bytes.Equal(got.Pixels, expected) {
				t.Fatal(tc.source, got, err)
			}
		}
	}
	data := xbmTestFile(9, 2, []byte("x01x01x80x00"))
	h, err := ReadXBMHeader(context.Background(), data, 512)
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodeXBM(context.Background(), h, data, 1024)
	expected := make([]byte, 18)
	expected[0] = 255
	expected[8] = 255
	expected[16] = 255
	if err != nil || !bytes.Equal(got.Pixels, expected) {
		t.Fatal("row reset", got, err)
	}
}

func TestXBMWindowBudgetAndCancellation(t *testing.T) {
	data := xbmTestFile(8, 1, []byte("xff"))
	data = append(data, bytes.Repeat([]byte{' '}, 600)...)
	for _, limit := range []int{0, 511, 512, 513} {
		h, err := ReadXBMHeader(context.Background(), data, limit)
		if limit < 512 {
			if !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
				t.Fatal("window budget", limit, err)
			}
		} else if err != nil || h.HeaderBytes != 512 {
			t.Fatal("exact window budget", h, err)
		}
	}
	for _, trigger := range []int{1, 2, 3} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if h, err := ReadXBMHeader(ctx, data, 512); !errors.Is(err, context.Canceled) || h.Width != 0 {
			t.Fatal("header cancellation", trigger, err)
		}
		cancel()
	}
}
func TestXBMPixelLimitsCancellationAndOwnership(t *testing.T) {
	for _, input := range [][]byte{
		xbmTestFile(256, 256, bytes.Repeat([]byte("xaa"), 8192)),
		xbmTestFile(1, 1, append(bytes.Repeat([]byte{' '}, 4<<20), []byte("xff")...)),
	} {
		h, err := ReadXBMHeader(context.Background(), input, 512)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeXBM(ctx, h, input, 8<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("scanner cancellation", trigger, err)
			}
			cancel()
		}
	}
	input := xbmTestFile(8, 1, []byte("xaa"))
	before := bytes.Clone(input)
	h, err := ReadXBMHeader(context.Background(), input, 512)
	if err != nil {
		t.Fatal(err)
	}
	for _, limit := range []int64{0, int64(len(input) + 7), int64(len(input) + 8)} {
		got, err := DecodeXBM(context.Background(), h, input, limit)
		if limit < int64(len(input)+8) {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("pixel budget", limit, err)
			}
		} else {
			if err != nil || !bytes.Equal(got.Pixels, []byte{0, 255, 0, 255, 0, 255, 0, 255}) {
				t.Fatal("exact budget", got, err)
			}
			got.Pixels[0] = 1
			if !bytes.Equal(before, input) {
				t.Fatal("output aliases input")
			}
		}
	}
	for _, field := range []string{"mode", "offset", "dimensions", "overflow"} {
		x := h
		switch field {
		case "mode":
			x.Mode = "RGB"
		case "offset":
			x.DataOffset = -1
		case "dimensions":
			x.Width = 0
		case "overflow":
			x.Width = int(^uint(0) >> 1)
			x.Height = 3
		}
		if got, err := DecodeXBM(context.Background(), x, input, 1024); err == nil || got.Pixels != nil {
			t.Fatal("invalid spec", field, err)
		}
	}
}
