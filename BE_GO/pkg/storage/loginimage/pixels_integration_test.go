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

func TestLoginImageAgainstPillow(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_IMAGE_PIXELS_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Policies []struct {
			Name                string
			Width, Height, Size int
			OutWidth            int `json:"out_width"`
			OutHeight           int `json:"out_height"`
			Keep, Invalid       bool
		}
		Pixels []struct {
			Name, Mode       string
			Width, Height    int
			Pixels, Expected []byte
			ExpectedJPEG     []byte `json:"expected_jpeg"`
			OutWidth         int    `json:"out_width"`
			OutHeight        int    `json:"out_height"`
			OutMode          string `json:"out_mode"`
		}
		URIs []struct {
			Input    string
			Bad      bool
			Expected []byte
		} `json:"data_uris"`
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Policies) == 0 || len(fixture.Pixels) == 0 || len(fixture.URIs) == 0 {
		t.Fatal("invalid image fixture")
	}
	for _, tc := range fixture.Policies {
		t.Run(tc.Name, func(t *testing.T) {
			plan, err := Decide(tc.Width, tc.Height, tc.Size)
			if tc.Invalid {
				if !errors.Is(err, ErrDimensions) {
					t.Fatal("zero-dimension fallback not detected", err)
				}
				return
			}
			if err != nil || plan.Width != tc.OutWidth || plan.Height != tc.OutHeight || plan.KeepOriginal != tc.Keep {
				t.Fatalf("Django decision differs: %+v %v", plan, err)
			}
		})
	}
	encoder, err := NewJPEGEncoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), jpegTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range fixture.Pixels {
		t.Run(tc.Name, func(t *testing.T) {
			input := Raster{tc.Width, tc.Height, tc.Mode, tc.Pixels}
			before := append([]byte(nil), tc.Pixels...)
			output, err := Transform(context.Background(), input, tc.OutWidth, tc.OutHeight, 64*1024*1024)
			if err != nil {
				t.Fatal(err)
			}
			if output.Mode != tc.OutMode || output.Width != tc.OutWidth || output.Height != tc.OutHeight || !bytes.Equal(output.Pixels, tc.Expected) {
				mismatch := 0
				for i := range output.Pixels {
					if i >= len(tc.Expected) || output.Pixels[i] != tc.Expected[i] {
						mismatch++
					}
				}
				t.Fatalf("Pillow pixels differ: %d mismatches, mode %s/%s", mismatch, output.Mode, tc.OutMode)
			}
			encoded, err := encoder.Encode(context.Background(), output)
			if err != nil || !bytes.Equal(encoded, tc.ExpectedJPEG) {
				t.Fatal("transformed JPEG differs", err)
			}
			if !bytes.Equal(tc.Pixels, before) {
				t.Fatal("source pixels mutated")
			}
		})
	}
	for i, tc := range fixture.URIs {
		decoded, err := DecodeDataURI(context.Background(), tc.Input, 1024)
		if tc.Bad {
			if decoded != nil || !errors.Is(err, ErrDataURI) {
				t.Fatalf("malformed vector %d accepted", i)
			}
		} else if err != nil || !bytes.Equal(decoded, tc.Expected) {
			t.Fatalf("base64 vector %d differs: %q (%v)", i, tc.Input, err)
		}
	}
	t.Run("concurrent transforms preserve exact output", func(t *testing.T) {
		tc := fixture.Pixels[26]
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				result, err := Transform(context.Background(), Raster{tc.Width, tc.Height, tc.Mode, tc.Pixels}, tc.OutWidth, tc.OutHeight, 64*1024*1024)
				if err != nil || !bytes.Equal(result.Pixels, tc.Expected) {
					t.Error("concurrent transform differs", err)
				}
			}()
		}
		wg.Wait()
	})
}
