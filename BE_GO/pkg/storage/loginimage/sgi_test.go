package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func sgiTestFile(w, h, c, bpc, compression int, payload []byte) []byte {
	b := make([]byte, 512)
	binary.BigEndian.PutUint16(b, 474)
	b[2] = byte(compression)
	b[3] = byte(bpc)
	dimension := 3
	if c == 1 {
		dimension = 2
	}
	for i, v := range []int{dimension, w, h, c} {
		binary.BigEndian.PutUint16(b[4+i*2:], uint16(v))
	}
	return append(b, payload...)
}
func sgiTestRLE(w, h int, rows [][]byte, lengths []uint32) []byte {
	pos := 512 + 8*h
	var table, body []byte
	for _, row := range rows {
		table = binary.BigEndian.AppendUint32(table, uint32(pos))
		pos += len(row)
		body = append(body, row...)
	}
	for i, row := range rows {
		n := uint32(len(row))
		if lengths != nil {
			n = lengths[i]
		}
		table = binary.BigEndian.AppendUint32(table, n)
	}
	return sgiTestFile(w, h, 1, 1, 1, append(table, body...))
}
func TestSGIRaw16AndLazyHeader(t *testing.T) {
	data := sgiTestFile(2, 1, 1, 2, 0, []byte{1, 255, 128, 3})
	h, err := ReadSGIHeader(context.Background(), data, 512)
	if err != nil || h.Mode != "L" || h.RawMode != "L;16B" || h.HeaderBytes != 512 || h.DataOffset != 512 {
		t.Fatal(h, err)
	}
	got, err := DecodeSGI(context.Background(), h, data, int64(len(data)+2))
	if err != nil || !bytes.Equal(got.Pixels, []byte{1, 128}) {
		t.Fatal(got, err)
	}
	if h, err := ReadSGIHeader(context.Background(), sgiTestFile(200, 100, 4, 2, 3, nil)[:12], 12); err != nil || h.Compression != 3 || h.Mode != "RGBA" || h.HeaderBytes != 12 {
		t.Fatal("lazy header", h, err)
	}
}
func TestSGIRowCarryAndEarlyStop(t *testing.T) {
	for _, tc := range []struct {
		lengths []uint32
		pixels  []byte
	}{
		{nil, []byte{99, 42, 42, 42, 42, 42}},
		{[]uint32{3, 1}, []byte{0, 0, 0, 42, 42, 42}},
		{[]uint32{3, 0}, []byte{42, 42, 42, 42, 42, 42}},
		{[]uint32{3, 0xffffffff}, []byte{42, 42, 42, 42, 42, 42}},
	} {
		data := sgiTestRLE(3, 2, [][]byte{{3, 42, 0}, {1, 99, 0}}, tc.lengths)
		h, err := ReadSGIHeader(context.Background(), data, 512)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeSGI(context.Background(), h, data, int64(len(data)+9))
		if err != nil || !bytes.Equal(got.Pixels, tc.pixels) {
			t.Fatal(tc.lengths, got, err)
		}
		if got, err := DecodeSGI(context.Background(), h, data, int64(len(data)+8)); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
			t.Fatal("row budget", err)
		}
	}
}

func TestSGIHeaderBudgetsAndCancellation(t *testing.T) {
	for _, length := range []int{12, 511, 512} {
		input := sgiTestFile(200, 100, 1, 1, 0, nil)[:length]
		for _, limit := range []int{0, length - 1, length} {
			h, err := ReadSGIHeader(context.Background(), input, limit)
			if limit < length {
				if !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
					t.Fatal("header budget", length, limit, err)
				}
			} else if err != nil || h.HeaderBytes != length || h.DataOffset != 512 {
				t.Fatal("exact header budget", h, err)
			}
		}
		for _, trigger := range []int{1, 2} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if h, err := ReadSGIHeader(ctx, input, 512); !errors.Is(err, context.Canceled) || h.Width != 0 {
				t.Fatal("header cancellation", trigger, err)
			}
			cancel()
		}
	}
}
func TestSGIPixelCancellationOwnershipAndValidation(t *testing.T) {
	rows := make([][]byte, 256)
	for i := range rows {
		rows[i] = []byte{127, 42, 127, 43, 2, 44, 0}
	}
	for _, input := range [][]byte{
		sgiTestFile(256, 256, 3, 1, 0, bytes.Repeat([]byte{42}, 256*256*3)),
		sgiTestRLE(256, 256, rows, nil),
		sgiTestRLE(1, 2048, make([][]byte, 2048), nil),
	} {
		h, err := ReadSGIHeader(context.Background(), input, 512)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeSGI(ctx, h, input, 1<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("decode cancellation", trigger, err)
			}
			cancel()
		}
	}
	input := sgiTestFile(1, 1, 3, 2, 0, []byte{10, 11, 20, 21, 30, 31})
	before := bytes.Clone(input)
	h, err := ReadSGIHeader(context.Background(), input, 512)
	if err != nil {
		t.Fatal(err)
	}
	for _, limit := range []int64{0, int64(len(input) + 2), int64(len(input) + 3)} {
		got, err := DecodeSGI(context.Background(), h, input, limit)
		if limit < int64(len(input)+3) {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("raw budget", limit, err)
			}
		} else {
			if err != nil || !bytes.Equal(got.Pixels, []byte{10, 20, 30}) {
				t.Fatal("exact raw budget", got, err)
			}
			got.Pixels[0] = 99
			if !bytes.Equal(input, before) {
				t.Fatal("output aliases input")
			}
		}
	}
	for _, field := range []string{"mode", "raw", "bpc", "channels", "dimension", "compression", "offset", "dimensions", "overflow"} {
		x := h
		switch field {
		case "mode":
			x.Mode = "P"
		case "raw":
			x.RawMode = "RGB"
		case "bpc":
			x.BPC = 3
		case "channels":
			x.Channels = 4
		case "dimension":
			x.Dimension = 1
		case "compression":
			x.Compression = 2
		case "offset":
			x.DataOffset = 513
		case "dimensions":
			x.Width = 0
		case "overflow":
			x.Width = int(^uint(0) >> 1)
			x.Height = 3
		}
		if got, err := DecodeSGI(context.Background(), x, input, 1<<20); err == nil || got.Pixels != nil {
			t.Fatal("invalid specification", field, err)
		}
	}
}
