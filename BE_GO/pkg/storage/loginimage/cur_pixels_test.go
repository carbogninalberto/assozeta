package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func TestCURMaskedPixelsBudgetsAndCancellation(t *testing.T) {
	// A three-entry gray palette selects L. Bottom-up XOR=73 and AND=127
	// produce luminance 37, alpha 128 after the two planes are composited.
	data := curTestFile(3, 1, 8, []byte{0, 0, 0, 0, 1, 1, 1, 0, 2, 2, 2, 0, 73, 73, 73, 0, 127, 127, 127})
	binary.LittleEndian.PutUint32(data[54:], 3)
	h, err := ReadCURHeader(context.Background(), data, len(data))
	if err != nil || h.Mode != "LA" || h.CURMaskHeight != 2 || h.Height != 1 {
		t.Fatal("gray CUR header", h, err)
	}
	before := bytes.Clone(data)
	limit := int64(len(data) + 12) // six source bytes plus six output bytes
	for _, budget := range []int64{limit - 1, limit, limit + 1} {
		got, err := DecodeCUR(context.Background(), h, data, budget)
		if budget < limit {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("masked allocation limit", err)
			}
		} else if err != nil || got.Mode != "LA" || !bytes.Equal(got.Pixels, []byte{37, 128, 37, 128, 37, 128}) {
			t.Fatal("masked pixel output", got, err)
		} else {
			got.Pixels[0] = 0
		}
	}
	if !bytes.Equal(data, before) {
		t.Fatal("output aliases input")
	}
	if got, err := DecodeCUR(context.Background(), h, data[:len(data)-1], limit); !errors.Is(err, ErrBMPDecode) || got.Pixels != nil {
		t.Fatal("partial AND row accepted", err)
	}
	for _, trigger := range []int{1, 3, 6, 7} {
		base, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: base, cancel: cancel, trigger: trigger}
		got, err := DecodeCUR(ctx, h, data, limit)
		cancel()
		if !errors.Is(err, context.Canceled) || got.Pixels != nil {
			t.Fatal("masked cancellation returned pixels", trigger, err)
		}
	}
	for _, height := range []int{-1, 1, 3, int(^uint(0) >> 1)} {
		bad := h
		bad.CURMaskHeight = height
		if got, err := DecodeCUR(context.Background(), bad, data, limit); !errors.Is(err, ErrBMPDecode) || got.Pixels != nil {
			t.Fatal("invalid mask dimensions", height, err)
		}
	}
}
