package loginimage

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.LoginImageProcessor = (*Processor)(nil)

func imageProcessorTestLimits() ProcessorLimits {
	return ProcessorLimits{Codec: JPEGLimits{MaxRasterBytes: 64 << 20, MaxEncodedBytes: 4 << 20, Concurrency: 2, Timeout: 5 * time.Second}, MaxURIBytes: 8 << 20, MaxHeaderBytes: 4 << 20, MaxTailBytes: 4 << 20, MaxDecodeBytes: 64 << 20, MaxTransformBytes: 64 << 20}
}
func imageURI(b []byte) string { return "opaque," + base64.StdEncoding.EncodeToString(b) }

func TestImageProcessorConstructorAndEarlyLimits(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"URI", "header", "tail", "decode", "transform", "raster", "encoded", "concurrency", "timeout"} {
		l := imageProcessorTestLimits()
		switch field {
		case "URI":
			l.MaxURIBytes = 23
		case "header":
			l.MaxHeaderBytes = 0
		case "tail":
			l.MaxTailBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		case "raster":
			l.Codec.MaxRasterBytes = 0
		case "encoded":
			l.Codec.MaxEncodedBytes = 0
		case "concurrency":
			l.Codec.Concurrency = 0
		case "timeout":
			l.Codec.Timeout = 0
		}
		if p, err := NewProcessor(path, l); p != nil || err == nil {
			t.Fatal("unbounded processor", field)
		}
	}
	l := imageProcessorTestLimits()
	l.MaxURIBytes = 128
	p, err := NewProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := p.CompressLoginImage(context.Background(), string(bytes.Repeat([]byte{'x'}, 129))); got != "" || !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("URI bound", err)
	}
	l.Codec.MaxEncodedBytes = 3
	p, err = NewProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	for _, n := range []int{3, 4} {
		value := imageURI(bytes.Repeat([]byte{127}, n))
		got, err := p.CompressLoginImage(context.Background(), value)
		if n == 3 {
			if got != value || err != nil {
				t.Fatal("exact decoded budget", err)
			}
		} else if got != "" || !errors.Is(err, ErrWorkingLimit) {
			t.Fatal("decoded bound", err)
		}
	}
}

func TestImageProcessorUnavailableCodecAndMalformedFallback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed executable"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewProcessor(path, imageProcessorTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"https://example.test/image", "opaque,A", imageURI([]byte("plain invalid bytes")), imageURI(gifTestFile(200, 100, nil))} {
		if got, err := p.CompressLoginImage(context.Background(), value); err != nil || got != value {
			t.Fatal("unchanged fallback requires codec", err)
		}
	}
	if got, err := p.CompressLoginImage(context.Background(), imageURI(gifTestFile(1, 1, []byte{73}))); got != "" || !errors.Is(err, ErrJPEGCodecUnavailable) {
		t.Fatal("missing codec hidden", err)
	}
	if len(p.slots) != 0 {
		t.Fatal("failed call retained global slot")
	}
}
func TestImageProcessorPreCancelledCallReturnsNoResult(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	p, err := NewProcessor(path, imageProcessorTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	value := imageURI(bytes.Repeat([]byte{127}, 1<<20))
	if got, err := p.CompressLoginImage(ctx, value); got != "" || !errors.Is(err, context.Canceled) {
		t.Fatal("pre-cancelled image", err)
	}
	if len(p.slots) != 0 {
		t.Fatal("cancellation retained global slot")
	}
}
