package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func gifTestFile(w, h int, pixels []byte) []byte {
	b := append([]byte("GIF89a"), make([]byte, 7)...)
	binary.LittleEndian.PutUint16(b[6:], uint16(w))
	binary.LittleEndian.PutUint16(b[8:], uint16(h))
	b = append(b, ',', 0, 0, 0, 0, byte(w), byte(w>>8), byte(h), byte(h>>8), 0, 8)
	var raw []byte
	var buf uint32
	bits := 0
	put := func(code uint32) {
		buf |= code << bits
		bits += 9
		for bits >= 8 {
			raw = append(raw, byte(buf))
			buf >>= 8
			bits -= 8
		}
	}
	for _, v := range pixels {
		put(256)
		put(uint32(v))
	}
	put(257)
	if bits > 0 {
		raw = append(raw, byte(buf))
	}
	for len(raw) > 0 {
		n := min(255, len(raw))
		b = append(b, byte(n))
		b = append(b, raw[:n]...)
		raw = raw[n:]
	}
	return append(b, 0, ';')
}

func TestGIFBudgetsOwnershipAndInvalidHeader(t *testing.T) {
	input := gifTestFile(3, 2, []byte{0, 1, 2, 3, 4, 255})
	before := bytes.Clone(input)
	h, err := ReadGIFHeader(context.Background(), input, 24)
	if err != nil || h.DataOffset != 24 {
		t.Fatal(h, err)
	}
	if got, err := ReadGIFHeader(context.Background(), input, 23); !errors.Is(err, ErrWorkingLimit) || got.Width != 0 {
		t.Fatal("header budget", err)
	}
	exact := int64(len(input) + 6 + 4096*4)
	got, err := DecodeGIF(context.Background(), h, input, exact)
	if err != nil || !bytes.Equal(got.Pixels, []byte{0, 1, 2, 3, 4, 255}) {
		t.Fatal(got, err)
	}
	got.Pixels[0] = 99
	if !bytes.Equal(input, before) {
		t.Fatal("aliased input")
	}
	if got, err := DecodeGIF(context.Background(), h, input, exact-1); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("decode budget", err)
	}
	for _, mutate := range []func(*GIFHeader){func(h *GIFHeader) { h.Width = int(^uint(0) >> 1) }, func(h *GIFHeader) { h.Left = -1 }, func(h *GIFHeader) { h.Right = h.Width + 1 }, func(h *GIFHeader) { h.Bottom = h.Top }, func(h *GIFHeader) { h.DataOffset = -1 }, func(h *GIFHeader) { h.DataOffset = len(input) + 1 }, func(h *GIFHeader) { h.Bits = 13 }, func(h *GIFHeader) { h.Mode = "RGB" }, func(h *GIFHeader) { h.Transparency = 256 }} {
		invalid := h
		mutate(&invalid)
		if got, err := DecodeGIF(context.Background(), invalid, input, 1<<30); !errors.Is(err, ErrGIFDecode) || got.Pixels != nil {
			t.Fatal("invalid header accepted", err)
		}
	}
	// Comments are independent of the input and charged in the header budget.
	ext := []byte{'!', 254, 3, 'a', 'b', 'c', 0}
	commented := append(bytes.Clone(input[:13]), ext...)
	commented = append(commented, input[13:]...)
	ch, err := ReadGIFHeader(context.Background(), commented, 24+len(ext)+3)
	if err != nil || string(ch.Comment) != "abc" {
		t.Fatal(ch, err)
	}
	ch.Comment[0] = 'z'
	if commented[16] != 'a' {
		t.Fatal("comment aliases input")
	}
	if _, err := ReadGIFHeader(context.Background(), commented, 24+len(ext)+2); !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("comment budget", err)
	}
}

func TestGIFCancellation(t *testing.T) {
	input := gifTestFile(128, 128, bytes.Repeat([]byte{71}, 128*128))
	h, err := ReadGIFHeader(context.Background(), input, 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := ReadGIFHeader(ctx, input, 1<<20); !errors.Is(err, context.Canceled) || got.Width != 0 {
		t.Fatal(err)
	}
	if got, err := DecodeGIF(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
		t.Fatal(err)
	}
	// Deterministic cancellation within header scanning and LZW processing.
	for _, stage := range []string{"header", "pixels", "fill"} {
		base, stop := context.WithCancel(context.Background())
		defer stop()
		ctx := &cancellingImageContext{Context: base, cancel: stop, trigger: 20}
		if stage == "header" {
			extended := append(bytes.Clone(input[:13]), bytes.Repeat([]byte{'x'}, 200)...)
			extended = append(extended, input[13:]...)
			if got, err := ReadGIFHeader(ctx, extended, 1<<20); !errors.Is(err, context.Canceled) || got.Width != 0 {
				t.Fatal(stage, err)
			}
		} else {
			if stage == "fill" {
				h.Transparency = 7
				ctx.trigger = 3
			}
			if got, err := DecodeGIF(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal(stage, err)
			}
		}
	}
}
