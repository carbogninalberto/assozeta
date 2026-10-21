package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func pcxTestFile(w, h, bits, planes, stride int, pixels []byte) []byte {
	b := make([]byte, 128)
	copy(b, []byte{10, 5, 1, byte(bits)})
	binary.LittleEndian.PutUint16(b[8:], uint16(w-1))
	binary.LittleEndian.PutUint16(b[10:], uint16(h-1))
	b[65] = byte(planes)
	binary.LittleEndian.PutUint16(b[66:], uint16(stride))
	return append(b, pixels...)
}
func TestPCXHeaderAndNarrowRGB(t *testing.T) {
	data := pcxTestFile(1, 1, 8, 3, 2, []byte{10, 1, 20, 2, 30, 3})
	h, err := ReadPCXHeader(context.Background(), data, 128)
	if err != nil || h.Mode != "RGB" || h.RawMode != "RGB;L" || h.Stride != 6 || h.DataOffset != 128 || h.HeaderBytes != 128 || h.Frames != 1 || h.Format != "PCX" {
		t.Fatal(h, err)
	}
	got, err := DecodePCX(context.Background(), h, data, 1024)
	if err != nil || !bytes.Equal(got.Pixels, []byte{10, 1, 20}) {
		t.Fatal("Pillow narrow padded RGB", got, err)
	}
	for _, limit := range []int64{int64(len(data) + 8), int64(len(data) + 9)} {
		got, err := DecodePCX(context.Background(), h, data, limit)
		if limit == int64(len(data)+8) {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("row budget", err)
			}
		} else if err != nil {
			t.Fatal("exact row budget", err)
		}
	}
	if got, err := ReadPCXHeader(context.Background(), data, 127); !errors.Is(err, ErrWorkingLimit) || got.Width != 0 {
		t.Fatal("header budget", err)
	}
}
func TestPCXRunAndDCXOffset(t *testing.T) {
	for _, tc := range []struct{ data, pixels []byte }{
		{[]byte{0xc0, 99, 0xc3, 42}, []byte{42, 42, 42}},
		{[]byte{0xc4, 42}, nil},
		{[]byte{1, 2, 0xc1}, nil},
	} {
		data := pcxTestFile(3, 1, 8, 1, 3, tc.data)
		h, err := ReadPCXHeader(context.Background(), data, 1024)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodePCX(context.Background(), h, data, 1024)
		if tc.pixels == nil {
			if !errors.Is(err, ErrPCXDecode) || got.Pixels != nil {
				t.Fatal("invalid run", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, tc.pixels) {
			t.Fatal(got, err)
		}
	}
	prefix := binary.LittleEndian.AppendUint32(nil, 0x3ade68b1)
	prefix = binary.LittleEndian.AppendUint32(prefix, 12)
	prefix = binary.LittleEndian.AppendUint32(prefix, 0)
	data := append(prefix, pcxTestFile(1, 1, 8, 1, 1, []byte{42})...)
	h, err := ReadPCXHeader(context.Background(), data, 1024)
	if err != nil || h.Format != "DCX" || h.DataOffset != 128 || h.HeaderBytes != 281 || h.Frames != 1 {
		t.Fatal("DCX absolute reset/overlapping reads", h, err)
	}
	got, err := DecodePCX(context.Background(), h, data, 1024)
	if err != nil || !bytes.Equal(got.Pixels, []byte{0}) {
		t.Fatal("DCX reads header byte at absolute 128", got, err)
	}
}

func TestPCXPaletteDirectoryBudgetsAndCancellation(t *testing.T) {
	palette := []byte{12}
	for i := 0; i < 256; i++ {
		palette = append(palette, byte(i), byte(i), byte(i))
	}
	data := append(pcxTestFile(1, 1, 8, 1, 1, []byte{42}), palette...)
	for _, limit := range []int{896, 897, 898} {
		h, err := ReadPCXHeader(context.Background(), data, limit)
		if limit < 897 {
			if !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
				t.Fatal("palette budget", err)
			}
		} else if err != nil || h.HeaderBytes != 897 || h.Mode != "L" || h.DataOffset != 128 {
			t.Fatal("palette boundary", h, err)
		}
	}
	prefix := binary.LittleEndian.AppendUint32(nil, dcxMagic)
	for i := 0; i < 1024; i++ {
		prefix = binary.LittleEndian.AppendUint32(prefix, 4100)
	}
	dcx := append(prefix, pcxTestFile(200, 100, 8, 3, 200, nil)...)
	if h, err := ReadPCXHeader(context.Background(), dcx, len(dcx)); err != nil || h.Frames != 1024 || h.HeaderBytes != len(dcx) || h.DataOffset != len(dcx) {
		t.Fatal("full directory", h, err)
	}
	if h, err := ReadPCXHeader(context.Background(), dcx, len(dcx)-1); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
		t.Fatal("directory budget", err)
	}
	for _, input := range [][]byte{data, dcx} {
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if h, err := ReadPCXHeader(ctx, input, len(input)*2); !errors.Is(err, context.Canceled) || h.Width != 0 {
				t.Fatal("header cancellation", trigger, err)
			}
			cancel()
		}
	}
}

func TestPCXPixelCancellationOwnershipAndValidation(t *testing.T) {
	for _, input := range [][]byte{
		pcxTestFile(256, 256, 8, 1, 256, bytes.Repeat([]byte{42}, 256*256)),
		pcxTestFile(1, 1, 8, 1, 1, append(bytes.Repeat([]byte{192, 99}, 1024), 42)),
	} {
		h, err := ReadPCXHeader(context.Background(), input, 1024)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodePCX(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("pixel cancellation", err)
			}
			cancel()
		}
	}
	input := pcxTestFile(1, 1, 8, 3, 1, []byte{10, 20, 30})
	before := bytes.Clone(input)
	h, err := ReadPCXHeader(context.Background(), input, 128)
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodePCX(context.Background(), h, input, 1024)
	if err != nil {
		t.Fatal(err)
	}
	got.Pixels[0] = 99
	if !bytes.Equal(before, input) {
		t.Fatal("output aliases input")
	}
	for _, field := range []string{"mode", "raw", "bits", "planes", "stride", "offset", "dimensions"} {
		x := h
		switch field {
		case "mode":
			x.Mode = "L"
		case "raw":
			x.RawMode = "bad"
		case "bits":
			x.Bits = 4
		case "planes":
			x.Planes = 4
		case "stride":
			x.Stride = 65536
		case "offset":
			x.DataOffset = -1
		case "dimensions":
			x.Width = 0
		}
		if got, err := DecodePCX(context.Background(), x, input, 1<<20); err == nil || got.Pixels != nil {
			t.Fatal("invalid specification", field, err)
		}
	}
	maximum := int(^uint(0) >> 1)
	h = PCXHeader{Width: maximum - 1, Height: 1, Bits: 1, Planes: 1, Mode: "1", RawMode: "1"}
	if got, err := DecodePCX(context.Background(), h, []byte{0}, int64(maximum)); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("packed row overflow", err)
	}
}
