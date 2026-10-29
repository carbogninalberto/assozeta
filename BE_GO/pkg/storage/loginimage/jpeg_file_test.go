package loginimage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func fileTestLimits() JPEGFileLimits {
	return JPEGFileLimits{Codec: jpegTestLimits(), MaxHeaderBytes: 1 << 20, MaxTransformBytes: 64 << 20}
}
func TestJPEGFileLazyPassthroughAndInvalidFallback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("test executable removed before processing"), 0700); err != nil {
		t.Fatal(err)
	}
	processor, err := NewJPEGFileProcessor(path, fileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, input := range [][]byte{jpegHeaderOnly(200, 100, 3), []byte{255, 216, 255, 1}} {
		result, err := processor.Process(context.Background(), input)
		if err != nil || !result.KeepOriginal || result.JPEG != nil {
			t.Fatal("lazy/fallback path invoked unavailable pixel codec", err)
		}
	}
	if result, err := processor.Process(context.Background(), jpegHeaderOnly(17, 13, 3)); result.KeepOriginal || result.JPEG != nil || !errors.Is(err, ErrJPEGCodecUnavailable) {
		t.Fatal("infrastructure failure incorrectly hidden as image fallback", err)
	}
	if result, err := processor.Process(context.Background(), []byte("PNG data belongs to another decoder")); result.KeepOriginal || result.JPEG != nil || !errors.Is(err, ErrNotJPEG) {
		t.Fatal("another format silently treated as JPEG", err)
	}
}
func TestJPEGFileAdmissionAndLimits(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"header", "transform"} {
		limits := fileTestLimits()
		if name == "header" {
			limits.MaxHeaderBytes = 0
		} else {
			limits.MaxTransformBytes = 0
		}
		if p, err := NewJPEGFileProcessor(path, limits); p != nil || !errors.Is(err, ErrJPEGLimits) {
			t.Fatal("unbounded file processor", err)
		}
	}
	limits := fileTestLimits()
	limits.Codec.Concurrency = 1
	limits.Codec.Timeout = 20 * time.Millisecond
	processor, err := NewJPEGFileProcessor(path, limits)
	if err != nil {
		t.Fatal(err)
	}
	processor.slots <- struct{}{}
	if result, err := processor.Process(context.Background(), jpegHeaderOnly(200, 100, 3)); result.KeepOriginal || result.JPEG != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("whole-pipeline admission not bounded", err)
	}
	if len(processor.slots) != 1 {
		t.Fatal("queue cancellation released another request's slot")
	}
	<-processor.slots
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if result, err := processor.Process(ctx, jpegHeaderOnly(200, 100, 3)); result.KeepOriginal || result.JPEG != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation turned into passthrough", err)
	}
}
