package loginimage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func qoiFileTestLimits() QOIFileLimits {
	return QOIFileLimits{jpegTestLimits(), 1 << 20, 64 << 20, 64 << 20}
}
func TestQOIFileLazyAndUnavailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed before processing"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewQOIFileProcessor(path, qoiFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, input := range [][]byte{qoiTestFile(200, 100, 3, nil), qoiTestFile(1, 1, 3, nil)} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("lazy or malformed fallback", err)
		}
	}
	input := qoiTestFile(1, 1, 3, []byte{254, 10, 20, 30})
	if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), []byte("another format")); !errors.Is(err, ErrNotQOI) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format dispatch", err)
	}
}
func TestQOIFileLimitsAndQueue(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"header", "decode", "transform"} {
		l := qoiFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewQOIFileProcessor(path, l); p != nil || !errors.Is(err, ErrQOIFileLimits) {
			t.Fatal("unbounded file processor", field, err)
		}
	}
	l := qoiFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewQOIFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), qoiTestFile(200, 100, 3, nil)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue timeout", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request's slot")
	}
	<-p.slots
}
