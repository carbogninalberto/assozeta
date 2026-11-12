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

func TestJPEGMetadataAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_JPEG_METADATA_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_jpeg_metadata.py", err)
	}
	var cases []struct {
		Name, Format   string
		Frames         int
		Data, Expected []byte
		Invalid        bool
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 236 {
		t.Fatal("invalid metadata fixture", err)
	}
	decoder, err := NewJPEGDecoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), jpegTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	encoder, err := NewJPEGEncoder(decoder.path, jpegTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			tc := cases[len(cases)-1]
			info, err := InspectJPEG(context.Background(), tc.Data, 1<<20)
			if err != nil || info.Format != "MPO" || info.Frames != 2 {
				t.Errorf("concurrent MPO inspection: %v", err)
			}
		}()
	}
	wg.Wait()
	for _, tc := range cases {
		info, err := InspectJPEG(context.Background(), tc.Data, 1<<20)
		if tc.Invalid {
			if info.Format != "" || !errors.Is(err, ErrJPEGHeader) {
				t.Errorf("%s: invalid metadata accepted: %v", tc.Name, err)
			}
			continue
		}
		if err != nil || info.Format != tc.Format || info.Frames != tc.Frames || info.Header.Width != 17 || info.Header.Height != 13 {
			t.Errorf("%s: admission differs: %s/%d %v", tc.Name, info.Format, info.Frames, err)
			continue
		}
		image, err := decoder.Decode(context.Background(), tc.Data)
		if err != nil {
			t.Errorf("%s: decode: %v", tc.Name, err)
			continue
		}
		result, err := encoder.EncodeWithComment(context.Background(), image.Raster, image.Comment)
		if err != nil || !bytes.Equal(result, tc.Expected) {
			t.Errorf("%s: first-frame JPEG differs: %v", tc.Name, err)
		}
	}
}
