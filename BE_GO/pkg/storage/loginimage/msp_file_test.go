package loginimage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func mspFileTestLimits() MSPFileLimits {
	return MSPFileLimits{jpegTestLimits(), 1 << 20, 64 << 20, 64 << 20}
}
func TestMSPFileLazyAndUnavailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed before processing"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewMSPFileProcessor(path, mspFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, input := range [][]byte{mspTestFile(200, 100, nil), mspTestFile(1, 1, nil)} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("lazy or malformed fallback", err)
		}
	}
	input := mspTestFile(1, 1, []byte{255})
	if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), []byte("another format")); !errors.Is(err, ErrNotMSP) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format dispatch", err)
	}
}
func TestMSPFileLimitsAndQueue(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"header", "decode", "transform"} {
		l := mspFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewMSPFileProcessor(path, l); p != nil || !errors.Is(err, ErrMSPFileLimits) {
			t.Fatal("unbounded file processor", field, err)
		}
	}
	l := mspFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewMSPFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), mspTestFile(200, 100, nil)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue timeout", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request's slot")
	}
	<-p.slots
}
