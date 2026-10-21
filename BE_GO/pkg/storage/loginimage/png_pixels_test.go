package loginimage

import (
	"bytes"
	"compress/zlib"
	"context"
	"errors"
	"testing"
)

func pngCompressed(t *testing.T, raw []byte) []byte {
	t.Helper()
	var out bytes.Buffer
	w := zlib.NewWriter(&out)
	if _, err := w.Write(raw); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}
func TestPNGScanlineDecodeAndLimits(t *testing.T) {
	spec := PNGPixelSpec{Width: 3, Height: 2, Depth: 8, Color: 0}
	data := pngCompressed(t, []byte{0, 1, 2, 3, 0, 4, 5, 6})
	result, err := DecodePNGScanlines(context.Background(), spec, data, 1<<20)
	if err != nil || result.Mode != "L" || result.Width != 3 || result.Height != 2 || !bytes.Equal(result.Pixels, []byte{1, 2, 3, 4, 5, 6}) {
		t.Fatal("wrong PNG pixels", result, err)
	}
	for _, bound := range []int64{0, -1, -1 << 63, 1, int64(len(data) + 6 + 6)} {
		got, err := DecodePNGScanlines(context.Background(), spec, data, bound)
		if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
			t.Fatal("PNG budget ignored", err)
		}
	}
	for _, extra := range []int64{0, 1} {
		if got, err := DecodePNGScanlines(context.Background(), spec, data, int64(len(data)+6+7)+extra); err != nil || !bytes.Equal(got.Pixels, result.Pixels) {
			t.Fatal("exact PNG budget rejected", err)
		}
	}
	for _, raw := range [][]byte{{5, 1, 2, 3, 0, 4, 5, 6}, {0, 1, 2, 3, 0, 4, 5}} {
		got, err := DecodePNGScanlines(context.Background(), spec, pngCompressed(t, raw), 1<<20)
		if !errors.Is(err, ErrPNGDecode) || got.Pixels != nil {
			t.Fatal("invalid PNG pixels accepted", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := DecodePNGScanlines(ctx, spec, data, 1<<20); got.Pixels != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled PNG returned pixels", err)
	}
}

func TestPNGInvalidSpecificationsAndMidDecodeCancellation(t *testing.T) {
	data := pngCompressed(t, []byte{0, 1})
	for _, spec := range []PNGPixelSpec{{Width: 1, Height: 1, Depth: 3, Color: 0}, {Width: 1, Height: 1, Depth: 8, Color: 5}, {Width: 1, Height: 1, Depth: 16, Color: 3}} {
		if got, err := DecodePNGScanlines(context.Background(), spec, data, 1<<20); got.Pixels != nil || !errors.Is(err, ErrPNGDecode) {
			t.Fatal("invalid PNG spec accepted", err)
		}
	}
	for _, spec := range []PNGPixelSpec{{Depth: 8, Color: 0}, {Width: -1, Height: 1, Depth: 8, Color: 0}, {Width: int(^uint(0) >> 1), Height: 2, Depth: 16, Color: 6}} {
		if got, err := DecodePNGScanlines(context.Background(), spec, data, 1<<20); got.Pixels != nil || err == nil {
			t.Fatal("invalid/overflowing dimensions accepted", err)
		}
	}
	raw := make([]byte, 1025*128)
	for y := 0; y < 128; y++ {
		for x := 1; x <= 1024; x++ {
			raw[y*1025+x] = byte(x * y)
		}
	}
	data = pngCompressed(t, raw)
	for _, trigger := range []int{2, 10, 100} {
		base, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: base, cancel: cancel, trigger: trigger}
		got, err := DecodePNGScanlines(ctx, PNGPixelSpec{Width: 1024, Height: 128, Depth: 8, Color: 0}, data, 1<<20)
		cancel()
		if got.Pixels != nil || !errors.Is(err, context.Canceled) {
			t.Fatal("mid-decode cancellation returned pixels", err)
		}
	}
}

func TestPNGScanlineEarlyStreamEnd(t *testing.T) {
	for _, tc := range []struct {
		name      string
		interlace bool
		raw, want []byte
	}{
		{"complete row", false, []byte{0, 1, 2, 3}, []byte{1, 2, 3, 0, 0, 0}},
		{"complete Adam7 row", true, []byte{0, 42}, []byte{42, 0, 0, 0, 0, 0}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			data := pngCompressed(t, tc.raw)
			spec := PNGPixelSpec{Width: 3, Height: 2, Depth: 8, Interlace: tc.interlace}
			got, err := DecodePNGScanlines(context.Background(), spec, data, 1<<20)
			if err != nil || got.Mode != "L" || !bytes.Equal(got.Pixels, tc.want) {
				t.Fatalf("valid early stream end: got %v, %v", got, err)
			}
			// Without a complete trailer this is truncated input, not a valid end.
			for _, broken := range [][]byte{data[:len(data)-4], pngCompressed(t, tc.raw[:len(tc.raw)-1])} {
				got, err = DecodePNGScanlines(context.Background(), spec, broken, 1<<20)
				if !errors.Is(err, ErrPNGDecode) || got.Pixels != nil {
					t.Fatalf("incomplete row/stream accepted: %v, %v", got, err)
				}
			}
		})
	}
}
