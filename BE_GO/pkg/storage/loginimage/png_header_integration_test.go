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

func TestPNGHeaderAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_PNG_HEADER_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_png_header.py", err)
	}
	var cases []struct {
		Name, Mode                           string
		Data, Comment                        []byte
		Invalid, Interlace, Default, Warning bool
		Width, Height, Depth, Color, Frames  int
		Tile                                 [4]int
		InvalidTile                          bool   `json:"invalid_tile"`
		HeaderBytes                          int    `json:"header_bytes"`
		DataLength                           int    `json:"data_length"`
		DataKind                             string `json:"data_kind"`
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 398 {
		t.Fatal("invalid PNG header fixture", err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for _, tc := range cases {
				if tc.Invalid || len(tc.Data) > 1024 {
					continue
				}
				h, err := ReadPNGHeader(context.Background(), tc.Data, 8<<20)
				if err != nil || h.Frames != tc.Frames || !bytes.Equal(h.Comment, tc.Comment) {
					t.Errorf("concurrent %s: %v", tc.Name, err)
				}
			}
		}()
	}
	wg.Wait()
	for _, tc := range cases {
		h, err := ReadPNGHeader(context.Background(), tc.Data, 8<<20)
		if tc.Invalid {
			if !errors.Is(err, ErrPNGHeader) || h.Width != 0 {
				t.Errorf("%s: invalid PNG header accepted: %v", tc.Name, err)
			}
			continue
		}
		if err != nil || h.Width != tc.Width || h.Height != tc.Height || h.Mode != tc.Mode || h.Depth != tc.Depth || h.Color != tc.Color || h.Interlace != tc.Interlace || h.Frames != tc.Frames || h.DefaultImage != tc.Default || h.PixelWarning != tc.Warning || h.HeaderBytes != tc.HeaderBytes || h.DataOffset != tc.HeaderBytes || h.DataLength != tc.DataLength || h.DataKind != tc.DataKind || h.InvalidTile != tc.InvalidTile || h.Tile != (PNGRect{tc.Tile[0], tc.Tile[1], tc.Tile[2], tc.Tile[3]}) || !bytes.Equal(h.Comment, tc.Comment) {
			t.Errorf("%s: header differs: %+v err=%v", tc.Name, h, err)
		}
	}
}
