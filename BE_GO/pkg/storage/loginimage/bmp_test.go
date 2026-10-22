package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func bmpTestFile(width, height, bits int, pixels []byte) []byte {
	data := make([]byte, 54)
	copy(data, "BM")
	binary.LittleEndian.PutUint32(data[10:], 54)
	binary.LittleEndian.PutUint32(data[14:], 40)
	binary.LittleEndian.PutUint32(data[18:], uint32(width))
	binary.LittleEndian.PutUint32(data[22:], uint32(height))
	binary.LittleEndian.PutUint16(data[26:], 1)
	binary.LittleEndian.PutUint16(data[28:], uint16(bits))
	return append(data, pixels...)
}
func TestBMPHeaderAndRawPixels(t *testing.T) {
	data := bmpTestFile(1, 2, 24, []byte{30, 20, 10, 0, 60, 50, 40})
	h, err := ReadBMPHeader(context.Background(), data, 54)
	if err != nil || h.Mode != "RGB" || h.RawMode != "BGR" || h.Width != 1 || h.Height != 2 || h.DataOffset != 54 || h.HeaderBytes != 54 || h.Stride != 4 || h.TopDown {
		t.Fatal(h, err)
	}
	got, err := DecodeBMP(context.Background(), h, data, 1024)
	if err != nil || !bytes.Equal(got.Pixels, []byte{40, 50, 60, 10, 20, 30}) {
		t.Fatal("BMP row order/final padding", got, err)
	}
	if got, err := DecodeBMP(context.Background(), h, data[:len(data)-1], 1024); !errors.Is(err, ErrBMPDecode) || got.Pixels != nil {
		t.Fatal("short pixels accepted", err)
	}
	if got, err := ReadBMPHeader(context.Background(), data, 53); !errors.Is(err, ErrWorkingLimit) || got.Width != 0 {
		t.Fatal("header budget ignored", err)
	}
	for _, delta := range []int64{-1, 0, 1} {
		got, err := DecodeBMP(context.Background(), h, data, int64(len(data)+6)+delta)
		if delta < 0 {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("pixel budget ignored", err)
			}
		} else if err != nil {
			t.Fatal("exact pixel budget", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := DecodeBMP(ctx, h, data, 1024); !errors.Is(err, context.Canceled) || got.Pixels != nil {
		t.Fatal("cancelled BMP", err)
	}
}
func TestBMPRLEDeltaUsesFirstPair(t *testing.T) {
	h := BMPHeader{Width: 3, Height: 2, Mode: "L", RawMode: "L", Bits: 8, Compression: 1, TopDown: true}
	// The first pair overshoots and zero-fills the remaining bounded output.
	data := []byte{1, 42, 0, 2, 99, 99, 1, 1, 1, 77}
	got, err := DecodeBMP(context.Background(), h, data, 1024)
	if err != nil || !bytes.Equal(got.Pixels, []byte{42, 0, 0, 0, 0, 0}) {
		t.Fatal("RLE delta", got, err)
	}
}

func TestBMPHeaderPaletteBudgets(t *testing.T) {
	data := bmpTestFile(3, 1, 8, []byte{0, 0, 0, 99, 1, 1, 1, 99, 2, 2, 2})
	binary.LittleEndian.PutUint32(data[46:], 3)
	binary.LittleEndian.PutUint32(data[10:], 65)
	for _, limit := range []int{64, 65, 66} {
		h, err := ReadBMPHeader(context.Background(), data, limit)
		if limit < 65 {
			if !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
				t.Fatal("palette budget", h, err)
			}
		} else if err != nil || h.Mode != "L" || h.RawMode != "L" || h.HeaderBytes != 65 || h.DataOffset != 65 {
			t.Fatal("partial palette header", h, err)
		}
	}
	// Palette-offset correction uses three bytes per entry for OS/2.
	core := make([]byte, 26)
	copy(core, "BM")
	binary.LittleEndian.PutUint32(core[10:], 26)
	binary.LittleEndian.PutUint32(core[14:], 12)
	binary.LittleEndian.PutUint16(core[18:], 3)
	binary.LittleEndian.PutUint16(core[20:], 1)
	binary.LittleEndian.PutUint16(core[24:], 1)
	core = append(core, 0, 0, 0, 255, 255, 255)
	h, err := ReadBMPHeader(context.Background(), core, 32)
	if err != nil || h.HeaderBytes != 32 || h.DataOffset != 32 || h.Mode != "1" {
		t.Fatal("OS/2 palette offset", h, err)
	}
	// Forty-byte bitfield headers consume three external masks, not alpha.
	data = bmpTestFile(1, 1, 16, nil)
	binary.LittleEndian.PutUint32(data[30:], 3)
	for _, mask := range []uint32{0xf800, 0x7e0, 0x1f} {
		data = binary.LittleEndian.AppendUint32(data, mask)
	}
	h, err = ReadBMPHeader(context.Background(), data, 66)
	if err != nil || h.HeaderBytes != 66 || h.DataOffset != 54 || h.RawMode != "BGR;16" {
		t.Fatal("external masks", h, err)
	}
	if h, err := ReadBMPHeader(context.Background(), data, 65); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
		t.Fatal("mask budget", err)
	}
}

func TestBMPMidOperationCancellation(t *testing.T) {
	palette := make([]byte, 65536*4)
	for i := 0; i < 65536; i++ {
		palette[i*4] = byte(i)
		palette[i*4+1] = byte(i)
		palette[i*4+2] = byte(i)
	}
	data := bmpTestFile(1, 1, 8, palette)
	binary.LittleEndian.PutUint32(data[46:], 65536)
	for _, trigger := range []int{2, 10, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if got, err := ReadBMPHeader(ctx, data, len(data)); !errors.Is(err, context.Canceled) || got.Width != 0 {
			t.Fatal("header cancellation", err)
		}
		cancel()
	}
	raw := bmpTestFile(1024, 128, 24, make([]byte, 1024*128*3))
	h, err := ReadBMPHeader(context.Background(), raw, 54)
	if err != nil {
		t.Fatal(err)
	}
	for _, rle := range []bool{false, true} {
		input := raw
		header := h
		if rle {
			input = bytes.Repeat([]byte{1, 42}, 1024*128)
			header = BMPHeader{Width: 1024 * 128, Height: 1, Mode: "L", Compression: 1, TopDown: true}
		}
		for _, trigger := range []int{2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeBMP(ctx, header, input, 4<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("pixel cancellation", rle, err)
			}
			cancel()
		}
	}
}

func TestBMPPixelValidationAndRLEBudget(t *testing.T) {
	h := BMPHeader{Width: 3, Height: 1, Mode: "L", Compression: 1, TopDown: true}
	data := []byte{3, 42}
	before := bytes.Clone(data)
	for _, limit := range []int64{0, 4, 5, 6} {
		got, err := DecodeBMP(context.Background(), h, data, limit)
		if limit < 5 {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("RLE budget", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, []byte{42, 42, 42}) {
			t.Fatal("RLE exact budget", err)
		}
	}
	if !bytes.Equal(data, before) {
		t.Fatal("RLE input mutated")
	}
	for _, field := range []string{"mode", "compression", "offset", "palette", "rawmode", "stride", "size"} {
		x := BMPHeader{Width: 1, Height: 1, Mode: "RGB", RawMode: "BGR", Stride: 4}
		switch field {
		case "mode":
			x.Mode = "LA"
		case "compression":
			x.Compression = 4
		case "offset":
			x.DataOffset = -1
		case "palette":
			x.InvalidPalette = true
		case "rawmode":
			x.RawMode = "RGBA"
		case "stride":
			x.Stride = 2
		case "size":
			x.Width = 0
		}
		if got, err := DecodeBMP(context.Background(), x, []byte{1, 2, 3, 0}, 1024); err == nil || got.Pixels != nil {
			t.Fatal("bad pixel spec", field, err)
		}
	}
}

func TestBMPRawRowArithmeticOverflow(t *testing.T) {
	maximum := int(^uint(0) >> 1)
	h := BMPHeader{Width: maximum / 8, Height: 1, Mode: "RGBA", RawMode: "RGBA"}
	got, err := DecodeBMP(context.Background(), h, []byte{1, 2, 3, 4}, int64(maximum))
	if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("row arithmetic overflow", err)
	}
}
