package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func gbrTestFile(w, h int, pixels []byte) []byte { return gbrTestLayout(w, h, 1, 1, nil, pixels) }
func gbrTestLayout(w, h, depth, version int, comment, pixels []byte) []byte {
	fixed := 20
	if version == 2 {
		fixed = 28
	}
	b := make([]byte, fixed)
	for i, v := range []uint32{uint32(fixed + len(comment)), uint32(version), uint32(w), uint32(h), uint32(depth)} {
		binary.BigEndian.PutUint32(b[i*4:], v)
	}
	if version == 2 {
		copy(b[20:], "GIMP")
	}
	b = append(b, comment...)
	return append(b, pixels...)
}
func TestGBRHeaderReadBoundaries(t *testing.T) {
	for _, version := range []int{1, 2} {
		for _, comment := range [][]byte{nil, []byte{0}, []byte("brush\x00"), []byte("unterminated")} {
			data := gbrTestLayout(3, 1, 1, version, comment, []byte{10, 20, 30})
			fixed := 20
			if version == 2 {
				fixed = 28
			}
			end := fixed + len(comment)
			h, err := ReadGBRHeader(context.Background(), data, end)
			if err != nil || h.Width != 3 || h.Height != 1 || h.Mode != "L" || h.Version != version || h.HeaderBytes != end || h.DataOffset != end || h.CommentOffset != fixed || h.CommentBytes != max(0, len(comment)-1) {
				t.Fatal(h, err)
			}
			if h, err := ReadGBRHeader(context.Background(), data, end-1); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
				t.Fatal("header budget", err)
			}
			got, err := DecodeGBR(context.Background(), h, data, int64(len(data)+3))
			if err != nil || !bytes.Equal(got.Pixels, []byte{10, 20, 30}) {
				t.Fatal(got, err)
			}
		}
	}
	for _, size := range []uint32{20, 21, 27, 28, 29, 0xffffffff} {
		data := gbrTestLayout(200, 100, 1, 2, []byte("partial"), nil)
		binary.BigEndian.PutUint32(data, size)
		h, err := ReadGBRHeader(context.Background(), data, len(data))
		consumed := len(data)
		if size >= 28 && size < uint32(len(data)) {
			consumed = int(size)
		}
		if err != nil || h.DataOffset != consumed || h.CommentBytes != max(0, consumed-29) {
			t.Fatal("negative or short read", size, h, err)
		}
	}
}
func TestGBRPixelBudgetOwnershipAndValidation(t *testing.T) {
	for _, depth := range []int{1, 4} {
		input := gbrTestLayout(3, 2, depth, 2, []byte("metadata\x00"), bytes.Repeat([]byte{45}, 6*depth))
		before := bytes.Clone(input)
		h, err := ReadGBRHeader(context.Background(), input, 1024)
		if err != nil {
			t.Fatal(err)
		}
		need := int64(len(input) + 6*depth)
		for _, limit := range []int64{0, need - 1, need} {
			got, err := DecodeGBR(context.Background(), h, input, limit)
			if limit < need {
				if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
					t.Fatal("budget", err)
				}
			} else {
				if err != nil || !bytes.Equal(got.Pixels, bytes.Repeat([]byte{45}, 6*depth)) {
					t.Fatal(got, err)
				}
				got.Pixels[0] = 0
				if !bytes.Equal(input, before) {
					t.Fatal("output aliases input")
				}
			}
		}
		for _, field := range []string{"offset", "mode", "depth", "version", "dimensions", "overflow"} {
			x := h
			switch field {
			case "offset":
				x.DataOffset = -1
			case "mode":
				x.Mode = "RGB"
			case "depth":
				x.Depth = 2
			case "version":
				x.Version = 3
			case "dimensions":
				x.Width = 0
			case "overflow":
				x.Width = int(^uint(0) >> 1)
				x.Height = 3
			}
			if got, err := DecodeGBR(context.Background(), x, input, 1024); err == nil || got.Pixels != nil {
				t.Fatal("invalid spec", field, err)
			}
		}
		if got, err := DecodeGBR(context.Background(), h, input[:len(input)-1], need); !errors.Is(err, ErrGBRDecode) || got.Pixels != nil {
			t.Fatal("short pixels", err)
		}
	}
}
func TestGBRCancellation(t *testing.T) {
	input := gbrTestLayout(1024, 1024, 1, 2, []byte("metadata\x00"), bytes.Repeat([]byte{45}, 1<<20))
	h, err := ReadGBRHeader(context.Background(), input, 1024)
	if err != nil {
		t.Fatal(err)
	}
	for _, trigger := range []int{1, 2, 3, 10, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if got, err := DecodeGBR(ctx, h, input, 4<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
			t.Fatal("decode cancellation", trigger, err)
		}
		cancel()
	}
	for _, trigger := range []int{1, 2, 3} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if h, err := ReadGBRHeader(ctx, input, 1024); !errors.Is(err, context.Canceled) || h.Width != 0 {
			t.Fatal("header cancellation", trigger, err)
		}
		cancel()
	}
}
