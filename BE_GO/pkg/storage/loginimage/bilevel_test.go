package loginimage

import (
	"bytes"
	"context"
	"errors"
	"strconv"
	"testing"
)

func TestBilevelNearestRetainsPillowRounding(t *testing.T) {
	for _, tc := range []struct {
		source        Raster
		width, height int
		want          []byte
	}{
		{Raster{2, 1, "1", []byte{0, 255}}, 7, 1, []byte{0, 0, 0, 0, 255, 255, 255}},
		{Raster{1, 2, "1", []byte{0, 255}}, 1, 7, []byte{0, 0, 0, 0, 255, 255, 255}},
		{Raster{2, 2, "1", []byte{0, 255, 255, 0}}, 3, 3, []byte{0, 255, 255, 255, 0, 0, 255, 0, 0}},
		{Raster{2, 2, "1", []byte{0, 255, 255, 0}}, 1, 1, []byte{0}},
	} {
		got, err := Transform(context.Background(), tc.source, tc.width, tc.height, 1<<20)
		if err != nil || got.Mode != "1" || !bytes.Equal(got.Pixels, tc.want) {
			t.Fatalf("nearest pixels differ: %v %v", got.Pixels, err)
		}
	}
}
func TestBilevelMemoryValidationAndOwnership(t *testing.T) {
	input := Raster{2, 1, "1", []byte{0, 255}}
	exact := int64(2 + 7 + 7*(strconv.IntSize/8))
	for _, delta := range []int64{-1, 0, 1} {
		got, err := Transform(context.Background(), input, 7, 1, exact+delta)
		if delta < 0 {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("nearest budget not enforced", err)
			}
		} else if err != nil {
			t.Fatal("exact nearest budget rejected", err)
		}
	}
	got, err := Transform(context.Background(), input, 2, 1, 4)
	if err != nil || !bytes.Equal(got.Pixels, input.Pixels) {
		t.Fatal("identity budget rejected", err)
	}
	got.Pixels[0] = 255
	if input.Pixels[0] != 0 {
		t.Fatal("identity aliases source")
	}
	for _, pixels := range [][]byte{{0}, {0, 1}, {0, 254}} {
		if got, err := Transform(context.Background(), Raster{2, 1, "1", pixels}, 7, 1, 1<<20); !errors.Is(err, ErrRaster) || got.Pixels != nil {
			t.Fatal("invalid normalized bilevel raster accepted", err)
		}
	}
	for _, trigger := range []int{2, 10, 50, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		got, err := Transform(ctx, Raster{128, 128, "1", make([]byte, 128*128)}, 200, 200, 1<<20)
		cancel()
		if !errors.Is(err, context.Canceled) || got.Pixels != nil {
			t.Fatal("nearest cancellation exposed pixels", err)
		}
	}
}
