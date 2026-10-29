//go:build imageintegration

package loginimage

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"sync"
	"testing"
	"time"
)

func TestBilevelAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_BILEVEL_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_bilevel.py", err)
	}
	var fixture struct {
		Pixels []struct {
			Name                   string
			Width, Height          int
			OutWidth               int `json:"out_width"`
			OutHeight              int `json:"out_height"`
			Pixels, Expected, JPEG []byte
		}
		Policy []struct {
			Name          string
			Data, JPEG    []byte
			Keep          bool
			Width, Height int
		}
	}
	if err := json.Unmarshal(data, &fixture); err != nil || len(fixture.Pixels) != 210 || len(fixture.Policy) != 18 {
		t.Fatal("invalid bilevel fixture", err)
	}
	limits := jpegTestLimits()
	limits.Timeout = 10 * time.Second
	encoder, err := NewJPEGEncoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := fixture.Pixels[i]
		original := bytes.Clone(tc.Pixels)
		got, err := Transform(context.Background(), Raster{tc.Width, tc.Height, "1", tc.Pixels}, tc.OutWidth, tc.OutHeight, 8<<20)
		if err != nil || got.Mode != "1" || got.Width != tc.OutWidth || got.Height != tc.OutHeight || !bytes.Equal(got.Pixels, tc.Expected) {
			t.Errorf("%s: nearest differs: %v", tc.Name, err)
			return
		}
		encoded, err := encoder.Encode(context.Background(), got)
		if err != nil || !bytes.Equal(encoded, tc.JPEG) {
			t.Errorf("%s: bilevel JPEG differs: %v", tc.Name, err)
		}
		if !bytes.Equal(original, tc.Pixels) {
			t.Error("bilevel input mutated")
		}
	}
	for i := range fixture.Pixels {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(19) }()
	}
	wg.Wait()
	for _, tc := range fixture.Policy {
		h, err := ReadPNGHeader(context.Background(), tc.Data, 1<<20)
		if err != nil {
			t.Fatal(tc.Name, err)
		}
		plan, err := Decide(h.Width, h.Height, len(tc.Data))
		if tc.Keep {
			if !plan.KeepOriginal && !errors.Is(err, ErrDimensions) {
				t.Error(tc.Name, "expected original")
			}
			continue
		}
		if err != nil || plan.KeepOriginal || plan.Width != tc.Width || plan.Height != tc.Height {
			t.Error(tc.Name, "size policy differs", err)
			continue
		}
		// These fixtures have one ordinary IDAT and no trailing metadata. Full PNG
		// file/chunk/frame processing remains a separate component requirement.
		source, err := DecodePNGScanlines(context.Background(), h.PNGPixelSpec, tc.Data[h.DataOffset:h.DataOffset+h.DataLength], 8<<20)
		if err != nil {
			t.Fatal(tc.Name, err)
		}
		resized, err := Transform(context.Background(), source, plan.Width, plan.Height, 8<<20)
		if err != nil {
			t.Fatal(tc.Name, err)
		}
		encoded, err := encoder.Encode(context.Background(), resized)
		if err != nil || !bytes.Equal(encoded, tc.JPEG) {
			t.Error(tc.Name, "PNG bilevel composition differs", err)
		}
	}
	for _, pixels := range [][]byte{{0, 1}, {0, 254}} {
		if got, err := encoder.Encode(context.Background(), Raster{2, 1, "1", pixels}); got != nil || !errors.Is(err, ErrRaster) {
			t.Fatal("encoder accepted non-normalized bilevel data", err)
		}
	}
	parent, cancel := context.WithCancel(context.Background())
	ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: 2}
	got, err := encoder.Encode(ctx, Raster{200, 100, "1", make([]byte, 200*100)})
	cancel()
	if got != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("bilevel encoder ignored validation cancellation", err)
	}
}
