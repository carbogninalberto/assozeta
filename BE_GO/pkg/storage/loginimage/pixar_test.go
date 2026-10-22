package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func pixarTestFile(w, h int) []byte {
	b := make([]byte, 1024+w*h*3)
	copy(b, "\x80\xe8\x00\x00")
	binary.LittleEndian.PutUint16(b[416:], uint16(h))
	binary.LittleEndian.PutUint16(b[418:], uint16(w))
	binary.LittleEndian.PutUint16(b[424:], 14)
	binary.LittleEndian.PutUint16(b[426:], 2)
	for i := 1024; i < len(b); i++ {
		b[i] = byte((i-1024)*37 + 11)
	}
	return b
}
func TestPIXARBudgetsAndOwnership(t *testing.T) {
	ctx := context.Background()
	b := pixarTestFile(3, 2)
	for _, n := range []int{428, 511, 512} {
		h, err := ReadPIXARHeader(ctx, b[:n], n)
		if err != nil || h != (PIXARHeader{3, 2, false}) {
			t.Fatal("exact header", n, h, err)
		}
		if _, err := ReadPIXARHeader(ctx, b[:n], n-1); !errors.Is(err, ErrWorkingLimit) {
			t.Fatal("header budget", n, err)
		}
	}
	h, err := ReadPIXARHeader(ctx, b, 512)
	if err != nil {
		t.Fatal(err)
	}
	budget := int64(len(b) + 18)
	if r, err := DecodePIXAR(ctx, h, b, budget-1); !errors.Is(err, ErrWorkingLimit) || r.Pixels != nil {
		t.Fatal("decode budget", err)
	}
	r, err := DecodePIXAR(ctx, h, b, budget)
	if err != nil || !bytes.Equal(r.Pixels, b[1024:]) {
		t.Fatal("pixels", err)
	}
	b[1024] ^= 255
	if bytes.Equal(r.Pixels, b[1024:]) {
		t.Fatal("input alias")
	}
	for _, bad := range []PIXARHeader{{-1, 2, false}, {0, 1, false}, {65536, 1, false}, {1, 65536, false}, {65535, 65535, false}} {
		if r, err := DecodePIXAR(ctx, bad, b, budget); !errors.Is(err, ErrPIXARDecode) || r.Pixels != nil {
			t.Fatal("forged dimensions", bad, err)
		}
	}
	b = pixarTestFile(257, 19)
	h, err = ReadPIXARHeader(ctx, b, 512)
	if err != nil {
		t.Fatal(err)
	}
	parent, cancel := context.WithCancel(ctx)
	defer cancel()
	controlled := &cancellingImageContext{Context: parent, cancel: cancel, trigger: 3}
	if r, err := DecodePIXAR(controlled, h, b, 1<<20); !errors.Is(err, context.Canceled) || r.Pixels != nil {
		t.Fatal("chunk cancellation", err)
	}
	if _, err := ReadPIXARHeader(parent, b, 512); !errors.Is(err, context.Canceled) {
		t.Fatal("precancelled header", err)
	}
}

func TestPIXARHeaderPixelCeiling(t *testing.T) {
	b := pixarTestFile(1, 1)
	for _, tc := range []struct {
		height            int
		warning, terminal bool
	}{{1365, false, false}, {1366, true, false}, {2730, true, false}, {2731, false, true}} {
		binary.LittleEndian.PutUint16(b[418:], 65535)
		binary.LittleEndian.PutUint16(b[416:], uint16(tc.height))
		h, err := ReadPIXARHeader(context.Background(), b, 512)
		if tc.terminal {
			if !errors.Is(err, ErrPIXARPixelLimit) || h.Width != 0 {
				t.Fatal("hard pixel ceiling", err)
			}
		} else if err != nil || h.Width != 65535 || h.Height != tc.height || h.PixelWarning != tc.warning {
			t.Fatal("pixel warning", h, err)
		}
	}
	binary.LittleEndian.PutUint16(b[424:], 0)
	if _, err := ReadPIXARHeader(context.Background(), b, 512); !errors.Is(err, ErrPIXARHeader) || errors.Is(err, ErrPIXARPixelLimit) {
		t.Fatal("mode rejection must precede pixel ceiling", err)
	}
}

func TestPIXARFileFallbackUnavailableAndLimits(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed executable"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewPIXARFileProcessor(path, bmpFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	b := pixarTestFile(3, 2)
	for _, input := range [][]byte{b[:427], b[:428], pixarTestFile(200, 3)[:428]} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("fallback required encoder", err)
		}
	}
	if got, err := p.Process(context.Background(), b); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), []byte("other")); !errors.Is(err, ErrNotPIXAR) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format selection", err)
	}
	path, err = os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"header", "decode", "transform"} {
		l := bmpFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewPIXARFileProcessor(path, l); p != nil || !errors.Is(err, ErrPIXARFileLimits) {
			t.Fatal("unbounded constructor", field, err)
		}
	}
	l := bmpFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err = NewPIXARFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), b); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue deadline", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request slot")
	}
	<-p.slots
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := p.Process(ctx, b); !errors.Is(err, context.Canceled) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("precancelled pipeline", err)
	}
}
