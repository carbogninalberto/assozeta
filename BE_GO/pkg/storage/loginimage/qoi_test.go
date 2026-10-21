package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func qoiTestFile(w, h, channels int, pixels []byte) []byte {
	b := []byte("qoif")
	b = binary.BigEndian.AppendUint32(b, uint32(w))
	b = binary.BigEndian.AppendUint32(b, uint32(h))
	b = append(b, byte(channels), 0)
	return append(b, pixels...)
}
func TestQOIHeaderLazySeekAndBudget(t *testing.T) {
	data := qoiTestFile(200, 100, 0, nil)[:13]
	h, err := ReadQOIHeader(context.Background(), data, 13)
	if err != nil || h.Width != 200 || h.Height != 100 || h.Mode != "RGBA" || h.DataOffset != 14 || h.HeaderBytes != 13 {
		t.Fatal(h, err)
	}
	if got, err := ReadQOIHeader(context.Background(), data, 12); !errors.Is(err, ErrWorkingLimit) || got.Width != 0 {
		t.Fatal("header limit", err)
	}
}
func TestQOIInitialIndexAndOvershoot(t *testing.T) {
	for _, tc := range []struct{ payload, pixels []byte }{
		{[]byte{53}, []byte{0, 0, 0, 255}},
		{[]byte{52}, []byte{0, 0, 0, 0}},
		{[]byte{253}, []byte{0, 0, 0, 255}},
		{[]byte{254, 10, 20, 30}, []byte{10, 20, 30, 255}},
		{[]byte{254, 10, 20}, nil},
	} {
		data := qoiTestFile(1, 1, 4, tc.payload)
		h, err := ReadQOIHeader(context.Background(), data, 13)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeQOI(context.Background(), h, data, int64(len(data)+4))
		if tc.pixels == nil {
			if !errors.Is(err, ErrQOIDecode) || got.Pixels != nil {
				t.Fatal("truncated packet", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, tc.pixels) {
			t.Fatal(got, err)
		}
	}
}

func TestQOIHeaderCancellation(t *testing.T) {
	data := qoiTestFile(1, 1, 3, []byte{192})
	for _, trigger := range []int{1, 2, 3, 4, 5, 6} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if h, err := ReadQOIHeader(ctx, data, 13); !errors.Is(err, context.Canceled) || h.Width != 0 {
			t.Fatal("header cancellation", trigger, err)
		}
		cancel()
	}
}
func TestQOIPixelBudgetsCancellationAndOwnership(t *testing.T) {
	for _, input := range [][]byte{
		qoiTestFile(256, 256, 3, bytes.Repeat([]byte{106}, 256*256)),
		qoiTestFile(256, 256, 4, bytes.Repeat([]byte{253}, 1058)),
	} {
		h, err := ReadQOIHeader(context.Background(), input, 13)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeQOI(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("pixel cancellation", trigger, err)
			}
			cancel()
		}
	}
	input := qoiTestFile(1, 1, 3, []byte{254, 10, 20, 30})
	before := bytes.Clone(input)
	h, err := ReadQOIHeader(context.Background(), input, 13)
	if err != nil {
		t.Fatal(err)
	}
	for _, limit := range []int64{0, int64(len(input) + 2), int64(len(input) + 3)} {
		got, err := DecodeQOI(context.Background(), h, input, limit)
		if limit < int64(len(input)+3) {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("pixel budget", limit, err)
			}
		} else {
			if err != nil || !bytes.Equal(got.Pixels, []byte{10, 20, 30}) {
				t.Fatal("exact budget", got, err)
			}
			got.Pixels[0] = 99
			if !bytes.Equal(input, before) {
				t.Fatal("output aliases input")
			}
		}
	}
	for _, field := range []string{"mode", "offset", "dimensions", "overflow"} {
		x := h
		switch field {
		case "mode":
			x.Mode = "L"
		case "offset":
			x.DataOffset = -1
		case "dimensions":
			x.Width = 0
		case "overflow":
			x.Width = int(^uint(0) >> 1)
			x.Height = 3
		}
		if got, err := DecodeQOI(context.Background(), x, input, 1<<20); err == nil || got.Pixels != nil {
			t.Fatal("invalid spec", field, err)
		}
	}
}

func TestQOIMixedCacheAlphaAndRunState(t *testing.T) {
	payload := []byte{254, 10, 20, 30, 255, 1, 2, 3, 127, 9, 64, 193, 23, 254, 7, 8, 9, 1, 254, 2, 4, 6}
	values := [][4]byte{{10, 20, 30, 255}, {1, 2, 3, 127}, {10, 20, 30, 255}, {8, 18, 28, 255}, {8, 18, 28, 255}, {8, 18, 28, 255}, {1, 2, 3, 127}, {7, 8, 9, 127}, {0, 0, 0, 0}, {2, 4, 6, 0}}
	for _, channels := range []int{3, 4} {
		input := qoiTestFile(5, 2, channels, payload)
		h, err := ReadQOIHeader(context.Background(), input, 13)
		if err != nil {
			t.Fatal(err)
		}
		var expected []byte
		for _, v := range values {
			expected = append(expected, v[:channels]...)
		}
		got, err := DecodeQOI(context.Background(), h, input, int64(len(input)+len(expected)))
		if err != nil || !bytes.Equal(got.Pixels, expected) {
			t.Fatal("mixed state", channels, got, err)
		}
	}
}
