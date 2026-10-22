package loginimage

import (
	"bytes"
	"context"
	"errors"
	"reflect"
	"testing"
)

func TestLanczosPassOrderAccountsForSelectedIntermediate(t *testing.T) {
	// RGB 7xH -> 3x2: vertical reduction below/equal/above twice the
	// horizontal reduction. The coefficient storage is included in each limit.
	for _, tc := range []struct{ height, budget int }{{9, 732}, {10, 778}, {11, 767}} {
		input := Raster{Width: 7, Height: tc.height, Mode: "RGB", Pixels: bytes.Repeat([]byte{73}, 7*tc.height*3)}
		for _, limit := range []int{tc.budget - 1, tc.budget} {
			got, err := Transform(context.Background(), input, 3, 2, int64(limit))
			if limit < tc.budget {
				if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
					t.Fatal("selected intermediate escaped budget", tc, err)
				}
			} else if err != nil || !bytes.Equal(got.Pixels, bytes.Repeat([]byte{73}, 18)) {
				t.Fatal("exact selected-intermediate budget", tc, err)
			}
		}
	}
}

func TestLoginImageDecision(t *testing.T) {
	for _, tc := range []struct {
		w, h, size, ow, oh int
		keep               bool
		invalid            bool
	}{
		{200, 100, 1048576, 200, 100, true, false}, {100, 200, 1, 100, 200, true, false},
		{100, 50, 1, 100, 50, false, false}, {400, 200, 1048576, 400, 200, false, false},
		{400, 200, 1048577, 200, 100, false, false}, {100, 50, 1048577, 200, 100, false, false},
		{1, 10000, 1, 1, 10000, false, false}, {1, 10000, 1048577, 0, 0, false, true},
		{0, 200, 1, 0, 0, false, true}, {200, 100, -1, 0, 0, false, true},
	} {
		p, err := Decide(tc.w, tc.h, tc.size)
		if tc.invalid {
			if !errors.Is(err, ErrDimensions) {
				t.Fatal("invalid dimensions accepted", err)
			}
			continue
		}
		if err != nil || p.Width != tc.ow || p.Height != tc.oh || p.KeepOriginal != tc.keep {
			t.Fatalf("decision for %dx%d/%d differs: %+v %v", tc.w, tc.h, tc.size, p, err)
		}
	}
}
func TestWhiteAlphaFlattenAndIdentityResize(t *testing.T) {
	input := Raster{Width: 4, Height: 1, Mode: "RGBA", Pixels: []byte{1, 2, 3, 0, 10, 20, 30, 128, 20, 30, 40, 255, 0, 0, 0, 1}}
	before := append([]byte(nil), input.Pixels...)
	result, err := Transform(context.Background(), input, 4, 1, 1024*1024)
	if err != nil || result.Mode != "RGB" || !reflect.DeepEqual(result.Pixels, []byte{255, 255, 255, 132, 137, 142, 20, 30, 40, 254, 254, 254}) {
		t.Fatal("white composition differs", result, err)
	}
	if !reflect.DeepEqual(input.Pixels, before) {
		t.Fatal("input was mutated")
	}
	result.Pixels[0] = 0
	if !reflect.DeepEqual(input.Pixels, before) {
		t.Fatal("output aliases input")
	}
	for _, mode := range []string{"L", "RGB", "CMYK", "YCbCr"} {
		channels := map[string]int{"L": 1, "RGB": 3, "CMYK": 4, "YCbCr": 3}[mode]
		pixels := make([]byte, 2*channels)
		for i := range pixels {
			pixels[i] = byte(i * 31)
		}
		result, err := Transform(context.Background(), Raster{2, 1, mode, pixels}, 2, 1, 1024*1024)
		if err != nil || result.Mode != mode || !reflect.DeepEqual(result.Pixels, pixels) {
			t.Fatal("identity conversion changed channels", err)
		}
	}
}
func TestPixelTransformRejectsInvalidWorkAndCancellation(t *testing.T) {
	for _, input := range []Raster{{0, 1, "RGB", nil}, {2, 1, "RGB", []byte{1}}, {1, 1, "P", []byte{1}}, {1 << 30, 1 << 30, "RGB", nil}} {
		result, err := Transform(context.Background(), input, 1, 1, 1024)
		if err == nil || result.Pixels != nil {
			t.Fatal("invalid raster accepted")
		}
	}
	valid := Raster{Width: 2, Height: 2, Mode: "RGB", Pixels: make([]byte, 12)}
	if result, err := Transform(context.Background(), valid, 200, 200, 100); !errors.Is(err, ErrWorkingLimit) || result.Pixels != nil {
		t.Fatal("budget not enforced", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if result, err := Transform(ctx, valid, 1, 1, 1024*1024); !errors.Is(err, context.Canceled) || result.Pixels != nil {
		t.Fatal("cancelled transform returned pixels", err)
	}
}

type cancellingImageContext struct {
	context.Context
	cancel          context.CancelFunc
	checks, trigger int
}

func (c *cancellingImageContext) Err() error {
	c.checks++
	if c.checks == c.trigger {
		c.cancel()
	}
	return c.Context.Err()
}
func TestCancellationDuringImageWorkReturnsNoPartialBuffer(t *testing.T) {
	input := Raster{Width: 128, Height: 128, Mode: "RGBA", Pixels: make([]byte, 128*128*4)}
	for _, tc := range []struct {
		name          string
		width, height int
	}{
		{"vertical-first-mixed", 200, 100},
		{"horizontal-first-mixed", 100, 200},
		{"vertical-first-reduction", 100, 64},
		{"horizontal-first-reduction", 64, 64},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Determine reachable checkpoints for each pass order. A fixed late
			// trigger may never fire when the first pass reduces the second's work.
			probe := &cancellingImageContext{Context: context.Background()}
			if _, err := Transform(probe, input, tc.width, tc.height, 16*1024*1024); err != nil || probe.checks < 1000 {
				t.Fatal("could not establish transform cancellation checkpoints", probe.checks, err)
			}
			for _, trigger := range []int{2, 4, 20, 100, 1000, probe.checks / 2, probe.checks - 1, probe.checks} {
				base, cancel := context.WithCancel(context.Background())
				ctx := &cancellingImageContext{Context: base, cancel: cancel, trigger: trigger}
				result, err := Transform(ctx, input, tc.width, tc.height, 16*1024*1024)
				cancel()
				if ctx.checks < trigger || !errors.Is(err, context.Canceled) || result.Pixels != nil {
					t.Fatal("mid-transform cancellation exposed pixels", trigger, ctx.checks, err)
				}
			}
		})
	}
}
func TestCoefficientBudgetCannotOverflow(t *testing.T) {
	source := Raster{Width: 1, Height: 1, Mode: "L", Pixels: []byte{1}}
	result, err := Transform(context.Background(), source, int(^uint(0)>>1)/16, 1, int64(^uint(0)>>1))
	if !errors.Is(err, ErrWorkingLimit) || result.Pixels != nil {
		t.Fatal("coefficient budget overflow accepted", err)
	}
}
