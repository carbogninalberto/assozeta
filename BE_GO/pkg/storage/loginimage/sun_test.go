package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func sunTestFile(w, h int, payload []byte) []byte { return sunTestLayout(w, h, 8, 1, nil, payload) }
func sunTestLayout(w, h, depth, kind int, palette, payload []byte) []byte {
	b := make([]byte, 32)
	for i, v := range []uint32{0x59a66a95, uint32(w), uint32(h), uint32(depth), 0, uint32(kind), 1, uint32(len(palette))} {
		binary.BigEndian.PutUint32(b[i*4:], v)
	}
	b = append(b, palette...)
	return append(b, payload...)
}
func TestSUNRawAndRLEAlignment(t *testing.T) {
	for _, tc := range []struct {
		depth, kind, w int
		payload, want  []byte
	}{
		{8, 1, 1, []byte{10, 99, 20}, []byte{10, 20}},
		{8, 2, 1, []byte{10, 20}, []byte{10, 20}},
		{1, 1, 1, []byte{128, 99, 0}, []byte{0, 255}},
		{1, 2, 1, []byte{128, 0, 0}, []byte{0, 255}},
		{4, 1, 3, []byte{0x12, 0x3f, 0x45, 0x6f}, []byte{17, 34, 51, 68, 85, 102}},
		{24, 1, 1, []byte{1, 2, 3, 99, 4, 5, 6}, []byte{3, 2, 1, 6, 5, 4}},
		{24, 3, 1, []byte{1, 2, 3, 99, 4, 5, 6}, []byte{1, 2, 3, 4, 5, 6}},
		{32, 1, 1, []byte{1, 2, 3, 255, 4, 5, 6, 0}, []byte{3, 2, 1, 6, 5, 4}},
		{8, 2, 3, []byte{128, 255, 75}, bytes.Repeat([]byte{75}, 6)},
	} {
		input := sunTestLayout(tc.w, 2, tc.depth, tc.kind, nil, tc.payload)
		h, err := ReadSUNHeader(context.Background(), input, 32)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeSUN(context.Background(), h, input, 1024)
		if err != nil || !bytes.Equal(got.Pixels, tc.want) {
			t.Fatal(tc, got, err)
		}
	}
}
func TestSUNHeaderPaletteAndBudget(t *testing.T) {
	for _, n := range []int{0, 1, 2, 768, 769, 770, 771, 1024} {
		input := sunTestLayout(3, 1, 4, 1, bytes.Repeat([]byte{22}, n), []byte{0x12, 0x30})
		h, err := ReadSUNHeader(context.Background(), input, 32+n)
		if err != nil || h.HeaderBytes != 32+n || h.DataOffset != 32+n || h.HasPalette != (n > 0) || h.InvalidPalette != (n >= 771) {
			t.Fatal(h, err)
		}
		if h, err := ReadSUNHeader(context.Background(), input, 31+n); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
			t.Fatal("header budget", err)
		}
		got, err := DecodeSUN(context.Background(), h, input, 4096)
		if n >= 771 {
			if !errors.Is(err, ErrSUNDecode) || got.Pixels != nil {
				t.Fatal("palette overflow", err)
			}
		} else {
			want := []byte{1, 2, 3}
			mode := "P"
			if n == 0 {
				want = []byte{17, 34, 51}
				mode = "L"
			}
			if err != nil || got.Mode != mode || !bytes.Equal(got.Pixels, want) {
				t.Fatal(got, err)
			}
		}
	}
	input := sunTestFile(200, 100, nil)
	binary.BigEndian.PutUint32(input[28:], 1024)
	h, err := ReadSUNHeader(context.Background(), input, 32)
	if err != nil || h.HeaderBytes != 32 || h.DataOffset != 1056 || h.Mode != "P" {
		t.Fatal("partial palette read", h, err)
	}
	for _, trigger := range []int{1, 2, 3} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if h, err := ReadSUNHeader(ctx, input, 32); !errors.Is(err, context.Canceled) || h.Width != 0 {
			t.Fatal("header cancellation", trigger, err)
		}
		cancel()
	}
}
func TestSUNPixelBudgetsCancellationAndOwnership(t *testing.T) {
	for _, kind := range []int{1, 2} {
		payload := []byte{10, 20}
		if kind == 1 {
			payload = []byte{10, 99, 20}
		}
		input := sunTestLayout(1, 2, 8, kind, nil, payload)
		before := bytes.Clone(input)
		h, err := ReadSUNHeader(context.Background(), input, 32)
		if err != nil {
			t.Fatal(err)
		}
		budget := len(input) + 2
		if kind == 2 {
			budget++
		}
		for _, limit := range []int{0, budget - 1, budget} {
			got, err := DecodeSUN(context.Background(), h, input, int64(limit))
			if limit < budget {
				if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
					t.Fatal("decode budget", kind, limit, err)
				}
			} else {
				if err != nil || !bytes.Equal(got.Pixels, []byte{10, 20}) {
					t.Fatal("exact budget", got, err)
				}
				got.Pixels[0] = 99
				if !bytes.Equal(input, before) {
					t.Fatal("aliased input")
				}
			}
		}
		for _, field := range []string{"mode", "rawmode", "offset", "dimensions", "overflow", "stride", "type", "depth"} {
			x := h
			switch field {
			case "mode":
				x.Mode = "RGBA"
			case "rawmode":
				x.RawMode = "RGB"
			case "offset":
				x.DataOffset = -1
			case "dimensions":
				x.Width = 0
			case "overflow":
				x.Width = int(^uint(0) >> 1)
				x.Height = 3
			case "stride":
				x.Stride = 0
			case "type":
				x.FileType = 6
			case "depth":
				x.Depth = 16
			}
			if got, err := DecodeSUN(context.Background(), x, input, 4096); err == nil || got.Pixels != nil {
				t.Fatal("invalid specification", field, err)
			}
		}
		payload = bytes.Repeat([]byte{50}, 65536)
		if kind == 2 {
			payload = bytes.Repeat([]byte{128, 255, 50}, 256)
		}
		input = sunTestLayout(256, 256, 8, kind, nil, payload)
		h, err = ReadSUNHeader(context.Background(), input, 32)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeSUN(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("decode cancellation", kind, trigger, err)
			}
			cancel()
		}
	}
}
