package loginimage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func sunFileTestLimits() SUNFileLimits {
	return SUNFileLimits{jpegTestLimits(), 1 << 20, 64 << 20, 64 << 20}
}
func TestSUNFileLazyAndUnavailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed before processing"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewSUNFileProcessor(path, sunFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, input := range [][]byte{sunTestFile(200, 100, nil), sunTestFile(1, 1, nil)} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("lazy or malformed fallback", err)
		}
	}
	input := sunTestFile(1, 1, []byte{255})
	if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), []byte("another format")); !errors.Is(err, ErrNotSUN) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format dispatch", err)
	}
}
func TestSUNFileLimitsAndQueue(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"header", "decode", "transform"} {
		l := sunFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewSUNFileProcessor(path, l); p != nil || !errors.Is(err, ErrSUNFileLimits) {
			t.Fatal("unbounded file processor", field, err)
		}
	}
	l := sunFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewSUNFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), sunTestFile(200, 100, nil)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue timeout", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request's slot")
	}
	<-p.slots
}
