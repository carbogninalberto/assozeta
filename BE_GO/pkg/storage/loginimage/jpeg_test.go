package loginimage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func jpegTestLimits() JPEGLimits {
	return JPEGLimits{MaxRasterBytes: 1 << 20, MaxEncodedBytes: 1 << 20, Concurrency: 2, Timeout: time.Second}
}
func TestJPEGEncoderRequiresExplicitLimitsAndExecutable(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"raster", "output", "concurrency", "timeout"} {
		limits := jpegTestLimits()
		switch name {
		case "raster":
			limits.MaxRasterBytes = 0
		case "output":
			limits.MaxEncodedBytes = 0
		case "concurrency":
			limits.Concurrency = 0
		case "timeout":
			limits.Timeout = 0
		}
		if encoder, err := NewJPEGEncoder(path, limits); encoder != nil || !errors.Is(err, ErrJPEGLimits) {
			t.Fatalf("%s: invalid limits accepted", name)
		}
	}
	for _, path := range []string{"codec", filepath.Join(t.TempDir(), "missing"), t.TempDir()} {
		if encoder, err := NewJPEGEncoder(path, jpegTestLimits()); encoder != nil || !errors.Is(err, ErrJPEGCodecUnavailable) {
			t.Fatal("invalid executable accepted", err)
		}
	}
	path = filepath.Join(t.TempDir(), "not-executable")
	if err := os.WriteFile(path, []byte("not executable"), 0600); err != nil {
		t.Fatal(err)
	}
	if encoder, err := NewJPEGEncoder(path, jpegTestLimits()); encoder != nil || !errors.Is(err, ErrJPEGCodecUnavailable) {
		t.Fatal("non-executable accepted", err)
	}
}
func TestJPEGEncoderRejectsInvalidRasterBeforeStartingProcess(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	limits := jpegTestLimits()
	limits.MaxRasterBytes = 4
	encoder, err := NewJPEGEncoder(path, limits)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		r    Raster
		want error
	}{
		{Raster{1, 1, "RGBA", make([]byte, 4)}, ErrUnsupportedMode},
		{Raster{1, 1, "P", []byte{0}}, ErrUnsupportedMode},
		{Raster{0, 1, "RGB", nil}, ErrDimensions},
		{Raster{65501, 1, "L", nil}, ErrDimensions},
		{Raster{1, 1, "RGB", []byte{0}}, ErrRaster},
		{Raster{2, 1, "RGB", make([]byte, 6)}, ErrWorkingLimit},
	} {
		if result, err := encoder.Encode(context.Background(), tc.r); result != nil || !errors.Is(err, tc.want) {
			t.Fatalf("%+v: %v", tc.r, err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if result, err := encoder.Encode(ctx, Raster{1, 1, "L", []byte{0}}); result != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation lost", err)
	}
}
func TestJPEGEncoderQueueUsesRequestDeadlineAndReleasesSlots(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	limits := jpegTestLimits()
	limits.Concurrency = 1
	limits.Timeout = 20 * time.Millisecond
	encoder, err := NewJPEGEncoder(path, limits)
	if err != nil {
		t.Fatal(err)
	}
	encoder.slots <- struct{}{}
	if result, err := encoder.Encode(context.Background(), Raster{1, 1, "L", []byte{0}}); result != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("queue not bounded", err)
	}
	if len(encoder.slots) != 1 {
		t.Fatal("queue cancellation released another request's slot")
	}
	<-encoder.slots
}
