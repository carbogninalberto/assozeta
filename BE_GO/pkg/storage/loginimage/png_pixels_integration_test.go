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
)

func TestPNGScanlinesAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_PNG_PIXELS_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_png_pixels.py", err)
	}
	var cases []struct {
		Name, Mode           string
		Spec                 PNGPixelSpec
		Data, Expected, JPEG []byte
		Invalid              bool
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 801 {
		t.Fatal("invalid PNG fixture", err)
	}
	encoder, err := NewJPEGEncoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), jpegTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := cases[i]
		original := bytes.Clone(tc.Data)
		got, err := DecodePNGScanlines(context.Background(), tc.Spec, tc.Data, 1<<20)
		if tc.Invalid {
			if !errors.Is(err, ErrPNGDecode) || got.Pixels != nil {
				t.Errorf("%s: invalid stream accepted: %v", tc.Name, err)
			}
			return
		}
		if err != nil || got.Width != tc.Spec.Width || got.Height != tc.Spec.Height || got.Mode != tc.Mode || !bytes.Equal(got.Pixels, tc.Expected) {
			t.Errorf("%s: PNG pixels differ: %v", tc.Name, err)
		}
		if err == nil {
			normalized := got
			// Same-size JPEG composition only. A resized mode-1 image still
			// needs its own nearest-neighbour path in the complete processor.
			if normalized.Mode == "1" {
				normalized.Mode = "L"
			}
			transformed, e := Transform(context.Background(), normalized, got.Width, got.Height, 8<<20)
			if tc.JPEG == nil {
				if !errors.Is(e, ErrUnsupportedMode) {
					t.Errorf("%s: unsupported JPEG mode admitted", tc.Name)
				}
			} else {
				if e != nil {
					t.Errorf("%s: pixel transform: %v", tc.Name, e)
				} else {
					encoded, e := encoder.Encode(context.Background(), transformed)
					if e != nil || !bytes.Equal(encoded, tc.JPEG) {
						t.Errorf("%s: composed JPEG differs: %v", tc.Name, e)
					}
				}
			}
		}
		if !bytes.Equal(original, tc.Data) {
			t.Error("PNG input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(749) }()
	}
	wg.Wait()
}
