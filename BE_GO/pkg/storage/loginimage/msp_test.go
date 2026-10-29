package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func mspTestFile(w, h int, payload []byte) []byte { return mspTestLayout(w, h, nil, payload) }
func mspTestLayout(w, h int, rows [][]byte, payload []byte) []byte {
	b := make([]byte, 32)
	copy(b, "DanM")
	if rows != nil {
		copy(b, "LinS")
	}
	binary.LittleEndian.PutUint16(b[4:], uint16(w))
	binary.LittleEndian.PutUint16(b[6:], uint16(h))
	var checksum uint16
	for i := 0; i < 32; i += 2 {
		checksum ^= binary.LittleEndian.Uint16(b[i:])
	}
	binary.LittleEndian.PutUint16(b[24:], checksum)
	if rows != nil {
		for _, row := range rows {
			b = binary.LittleEndian.AppendUint16(b, uint16(len(row)))
		}
		for _, row := range rows {
			b = append(b, row...)
		}
	}
	return append(b, payload...)
}
func TestMSPHeaderChecksumBudgetCancellation(t *testing.T) {
	data := mspTestFile(8, 2, []byte{128, 64})
	h, err := ReadMSPHeader(context.Background(), data, 32)
	if err != nil || h.Width != 8 || h.Height != 2 || h.HeaderBytes != 32 || h.Mode != "1" || h.RLE {
		t.Fatal(h, err)
	}
	if h, err := ReadMSPHeader(context.Background(), data, 31); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 {
		t.Fatal("header budget", err)
	}
	for i := 0; i < 32; i++ {
		corrupt := bytes.Clone(data)
		corrupt[i] ^= 1
		if h, err := ReadMSPHeader(context.Background(), corrupt, 32); !errors.Is(err, ErrMSPHeader) || h.Width != 0 {
			t.Fatal("checksum", i, err)
		}
	}
	for _, trigger := range []int{1, 2, 3} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if h, err := ReadMSPHeader(ctx, data, 32); !errors.Is(err, context.Canceled) || h.Width != 0 {
			t.Fatal("header cancellation", trigger, err)
		}
		cancel()
	}
}
func TestMSPConcatenatedRowsAndSurplusValidation(t *testing.T) {
	for _, tc := range []struct {
		rows [][]byte
		want []byte
	}{
		{[][]byte{{}, {}}, bytes.Repeat([]byte{255}, 16)},
		{[][]byte{{2, 128, 64}, {0, 0, 255}}, []byte{255, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 0, 0, 0, 0}},
		{[][]byte{{0, 0, 255}, {2, 128, 64}}, []byte{255, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 0, 0, 0, 0}},
		{[][]byte{{8, 128}, {1, 64}}, []byte{255, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 0, 0, 0, 0}},
		{[][]byte{{0, 4, 255}, {1}}, bytes.Repeat([]byte{255}, 16)},
		{[][]byte{{0, 4, 255}, {0}}, nil},
		{[][]byte{{0, 4, 255}, {0, 0}}, nil},
		{[][]byte{{0, 0, 255}, {0, 0, 255}}, nil},
	} {
		input := mspTestLayout(8, 2, tc.rows, nil)
		h, err := ReadMSPHeader(context.Background(), input, 32)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeMSP(context.Background(), h, input, 1024)
		if tc.want == nil {
			if !errors.Is(err, ErrMSPDecode) || got.Pixels != nil {
				t.Fatal("invalid RLE", tc.rows, err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, tc.want) {
			t.Fatal(tc.rows, got, err)
		}
	}
}
func TestMSPPixelLimitsOwnershipAndCancellation(t *testing.T) {
	for _, input := range [][]byte{mspTestFile(3, 2, []byte{160, 64}), mspTestLayout(3, 2, [][]byte{{1, 160}, {1, 64}}, nil)} {
		before := bytes.Clone(input)
		h, err := ReadMSPHeader(context.Background(), input, 32)
		if err != nil {
			t.Fatal(err)
		}
		budget := int64(len(input) + 6)
		for _, limit := range []int64{0, budget - 1, budget} {
			got, err := DecodeMSP(context.Background(), h, input, limit)
			if limit < budget {
				if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
					t.Fatal("decode budget", limit, err)
				}
			} else {
				if err != nil || !bytes.Equal(got.Pixels, []byte{255, 0, 255, 0, 255, 0}) {
					t.Fatal(got, err)
				}
				got.Pixels[0] = 0
				if !bytes.Equal(input, before) {
					t.Fatal("aliased input")
				}
			}
		}
		for _, field := range []string{"mode", "dimensions", "overflow"} {
			x := h
			switch field {
			case "mode":
				x.Mode = "L"
			case "dimensions":
				x.Width = 0
			case "overflow":
				x.Width = int(^uint(0) >> 1)
				x.Height = 3
			}
			if got, err := DecodeMSP(context.Background(), x, input, 1024); err == nil || got.Pixels != nil {
				t.Fatal("invalid specification", field, err)
			}
		}
	}
	rows := make([][]byte, 1024)
	for i := range rows {
		rows[i] = []byte{0, 128, 255}
	}
	for _, input := range [][]byte{mspTestFile(1024, 1024, bytes.Repeat([]byte{255}, 128*1024)), mspTestLayout(1024, 1024, rows, nil), mspTestLayout(1, 1, [][]byte{bytes.Repeat([]byte{0, 255, 255}, 20000)}, nil)} {
		h, err := ReadMSPHeader(context.Background(), input, 32)
		if err != nil {
			t.Fatal(err)
		}
		for _, trigger := range []int{1, 2, 10, 100} {
			parent, cancel := context.WithCancel(context.Background())
			ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
			if got, err := DecodeMSP(ctx, h, input, 4<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
				t.Fatal("raw/RLE/surplus cancellation", trigger, err)
			}
			cancel()
		}
	}
}
