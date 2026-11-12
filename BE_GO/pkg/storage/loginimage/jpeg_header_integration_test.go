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

func TestJPEGMarkerAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_JPEG_HEADER_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_jpeg_header.py", err)
	}
	var cases []struct {
		Name, Mode       string
		Data, Comment    []byte
		Width, Height    int
		Warning, Invalid bool
		HeaderBytes      int `json:"header_bytes"`
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 1364 {
		t.Fatal("invalid marker fixture", err)
	}
	for _, tc := range cases {
		got, err := ReadJPEGMarkerHeader(context.Background(), tc.Data, 1<<20)
		if tc.Invalid {
			if !errors.Is(err, ErrJPEGHeader) || got.Width != 0 {
				t.Errorf("%s: invalid header accepted: %v", tc.Name, err)
			}
			continue
		}
		if err != nil || got.Width != tc.Width || got.Height != tc.Height || got.Mode != tc.Mode || got.PixelWarning != tc.Warning || got.HeaderBytes != tc.HeaderBytes || !bytes.Equal(got.Comment, tc.Comment) || (got.Comment == nil) != (tc.Comment == nil) {
			t.Errorf("%s: header differs: %+v / %v", tc.Name, got, err)
		}
	}
	var wg sync.WaitGroup
	tc := cases[0]
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got, err := ReadJPEGMarkerHeader(context.Background(), tc.Data, 1<<20)
			if err != nil || got.Width != tc.Width || got.Mode != tc.Mode {
				t.Error("concurrent header differs", err)
			}
		}()
	}
	wg.Wait()
}
