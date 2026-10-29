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

type jpegFileCase struct {
	Name                         string
	Data, Expected, ExpectedJPEG []byte
	Keep, Invalid, EncodeInvalid bool
}

func jpegFileCases(t *testing.T, variable string, count int) []jpegFileCase {
	t.Helper()
	data, err := os.ReadFile(os.Getenv(variable))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_jpeg_file.py", err)
	}
	// The decoder fixture uses Python's snake_case field names.
	var raw []struct {
		Name           string
		Data, Expected []byte
		ExpectedJPEG   []byte `json:"expected_jpeg"`
		Keep, Invalid  bool
		EncodeInvalid  bool `json:"encode_invalid"`
	}
	if err := json.Unmarshal(data, &raw); err != nil || len(raw) != count {
		t.Fatal("invalid file fixture", err)
	}
	result := make([]jpegFileCase, len(raw))
	for i, v := range raw {
		result[i] = jpegFileCase{v.Name, v.Data, v.Expected, v.ExpectedJPEG, v.Keep, v.Invalid, v.EncodeInvalid}
	}
	return result
}
func TestJPEGFileAgainstPillow(t *testing.T) {
	limits := fileTestLimits()
	limits.Codec.MaxEncodedBytes = 2 << 20
	limits.Codec.Timeout = 30 * time.Second
	processor, err := NewJPEGFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	cases := jpegFileCases(t, "ASSOZETA_JPEG_FILE_FIXTURE", 97)
	for _, tc := range jpegFileCases(t, "ASSOZETA_JPEG_METADATA_FIXTURE", 236) {
		tc.Keep = tc.Invalid
		cases = append(cases, tc)
	}
	for _, tc := range jpegFileCases(t, "ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE", 1870) {
		tc.Keep = tc.Invalid || tc.EncodeInvalid
		tc.Expected = tc.ExpectedJPEG
		cases = append(cases, tc)
	}
	check := func(tc jpegFileCase) {
		t.Helper()
		original := bytes.Clone(tc.Data)
		result, err := processor.Process(context.Background(), tc.Data)
		if !bytes.HasPrefix(tc.Data, []byte{255, 216, 255}) {
			if !errors.Is(err, ErrNotJPEG) || result.KeepOriginal || result.JPEG != nil {
				t.Errorf("%s: format dispatch differs: %v", tc.Name, err)
			}
			return
		}
		if err != nil || result.KeepOriginal != tc.Keep || tc.Keep && result.JPEG != nil || !tc.Keep && !bytes.Equal(result.JPEG, tc.Expected) {
			t.Errorf("%s: file output/fallback differs: keep=%v want=%v err=%v", tc.Name, result.KeepOriginal, tc.Keep, err)
		}
		if !bytes.Equal(original, tc.Data) {
			t.Errorf("%s: input mutated", tc.Name)
		}
	}
	for _, tc := range cases {
		check(tc)
	}
	var wg sync.WaitGroup
	// Share one input requiring a resize and one processor across requests.
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(cases[11]) }()
	}
	wg.Wait()
	// Every stage rejects its own exhausted logical budget without returning an
	// original-image fallback. A header-only passthrough does not need raster space.
	for _, stage := range []string{"input", "header", "raster", "transform"} {
		bounded := limits
		switch stage {
		case "input":
			bounded.Codec.MaxEncodedBytes = 3
		case "header":
			bounded.MaxHeaderBytes = 3
		case "raster":
			bounded.Codec.MaxRasterBytes = 1
		case "transform":
			bounded.MaxTransformBytes = 1
		}
		p, err := NewJPEGFileProcessor(processor.decoder.path, bounded)
		if err != nil {
			t.Fatal(err)
		}
		got, err := p.Process(context.Background(), cases[9].Data)
		if !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Errorf("%s budget hidden as fallback: %v", stage, err)
		}
	}
}

func TestJPEGFilePipelineCancellation(t *testing.T) {
	limits := fileTestLimits()
	limits.Codec.Concurrency = 1
	limits.Codec.Timeout = 10 * time.Second
	p, err := NewJPEGFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	var input []byte
	for _, tc := range jpegFileCases(t, "ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE", 1870) {
		info, e := InspectJPEG(context.Background(), tc.Data, 1<<20)
		if e == nil && !tc.Invalid && info.Header.Width == 1 && info.Header.Height == 1 && info.Header.Mode == "L" {
			input = tc.Data
			break
		}
	}
	if input == nil {
		t.Fatal("missing single-pixel fixture")
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	p.encoder, err = NewJPEGEncoder(fault, limits.Codec)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		result, err := p.Process(ctx, input)
		if result.JPEG != nil || result.KeepOriginal {
			done <- errors.New("cancelled pipeline returned output")
			return
		}
		done <- err
	}()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if data, err := os.ReadFile(fault + ".started"); err == nil && len(data) > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("pipeline encoder did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer stop()
	result, err := p.Process(queued, jpegHeaderOnly(200, 100, 3))
	if !errors.Is(err, context.DeadlineExceeded) || result.KeepOriginal || result.JPEG != nil {
		t.Fatal("whole-pipeline admission released before encode", err)
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("pipeline cancellation lost", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("pipeline child was not reaped")
	}
	if len(p.slots) != 0 || len(p.decoder.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("pipeline cancellation retained admission")
	}
	result, err = p.Process(context.Background(), jpegHeaderOnly(200, 100, 3))
	if err != nil || !result.KeepOriginal {
		t.Fatal("pipeline unusable after cancellation", err)
	}
}
