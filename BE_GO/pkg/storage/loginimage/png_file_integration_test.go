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

func TestPNGFileAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_PNG_FILE_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_png_file.py", err)
	}
	var cases []struct {
		Name       string
		Data, JPEG []byte
		Keep       bool
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 1472 {
		t.Fatal("invalid PNG file fixture", err, len(cases))
	}
	limits := pngFileTestLimits()
	limits.Codec.MaxEncodedBytes = 4 << 20
	limits.Codec.Timeout = 30 * time.Second
	processor, err := NewPNGFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := cases[i]
		before := bytes.Clone(tc.Data)
		result, err := processor.Process(context.Background(), tc.Data)
		if err != nil || result.KeepOriginal != tc.Keep || tc.Keep && result.JPEG != nil || !tc.Keep && !bytes.Equal(result.JPEG, tc.JPEG) {
			t.Errorf("%s: file differs: keep=%v want=%v err=%v", tc.Name, result.KeepOriginal, tc.Keep, err)
		}
		if !bytes.Equal(before, tc.Data) {
			t.Error("file input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(744) }()
	}
	wg.Wait()
	if len(processor.slots) != 0 || len(processor.decoder.process.slots) != 0 || len(processor.encoder.slots) != 0 {
		t.Fatal("pipeline retained admission")
	}
}

func TestPNGFileStageBudgetsAndCancellation(t *testing.T) {
	input := append([]byte("\x89PNG\r\n\x1a\n"), pngChunk("IHDR", pngIHDR(1, 1, 8, 0))...)
	input = append(input, pngChunk("IDAT", pngCompressed(t, []byte{0, 42}))...)
	input = append(input, pngChunk("IEND", nil)...)
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	for _, stage := range []string{"input", "header", "tail", "decode", "transform", "encode"} {
		limits := pngFileTestLimits()
		switch stage {
		case "input":
			limits.Codec.MaxEncodedBytes = len(input) - 1
		case "header":
			limits.MaxHeaderBytes = 40
		case "tail":
			limits.MaxTailBytes = 11
		case "decode":
			limits.MaxDecodeBytes = 1
		case "transform":
			limits.MaxTransformBytes = 1
		case "encode":
			limits.Codec.MaxEncodedBytes = 100
		}
		p, err := NewPNGFileProcessor(path, limits)
		if err != nil {
			t.Fatal(err)
		}
		got, err := p.Process(context.Background(), input)
		if !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage budget hidden as fallback", stage, err)
		}
	}
	limits := pngFileTestLimits()
	limits.Codec.Concurrency = 1
	limits.Codec.Timeout = 5 * time.Second
	p, err := NewPNGFileProcessor(path, limits)
	if err != nil {
		t.Fatal(err)
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	p.encoder, err = NewJPEGEncoder(fault, limits.Codec)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		got, err := p.Process(ctx, input)
		if got.KeepOriginal || got.JPEG != nil {
			done <- errors.New("cancelled pipeline exposed result")
			return
		}
		done <- err
	}()
	deadline := time.Now().Add(2 * time.Second)
	for {
		if b, err := os.ReadFile(fault + ".started"); err == nil && len(b) > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("encoder did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	// A lazy passthrough would finish immediately if admission had been
	// released after decoding but before the stalled encoder completes.
	lazy := append([]byte("\x89PNG\r\n\x1a\n"), pngChunk("IHDR", pngIHDR(200, 100, 8, 2))...)
	lazy = append(lazy, []byte{0, 0, 0, 100, 'I', 'D', 'A', 'T'}...)
	got, err := p.Process(queued, lazy)
	stop()
	if !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("whole pipeline queue cancellation", err)
	}
	if b, err := os.ReadFile(fault + ".started"); err != nil || string(b) != "1" {
		t.Fatal("queued pipeline spawned another child")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("encoder not reaped")
	}
	if len(p.slots) != 0 || len(p.decoder.process.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("cancellation leaked slot")
	}
	p.encoder, err = NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		t.Fatal(err)
	}
	got, err = p.Process(context.Background(), input)
	if err != nil || got.KeepOriginal || len(got.JPEG) == 0 {
		t.Fatal("pipeline not reusable", err)
	}
}
