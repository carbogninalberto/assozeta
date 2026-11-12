package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func curFileTestLimits() CURFileLimits { return bmpFileTestLimits() }
func curTestFile(w, h, bits int, pixels []byte) []byte {
	data := make([]byte, 22)
	copy(data, []byte{0, 0, 2, 0, 1, 0})
	data[6] = byte(w)
	data[7] = byte(h)
	binary.LittleEndian.PutUint32(data[18:], 22)
	return append(data, bmpTestFile(w, h*2, bits, pixels)[14:]...)
}
func TestCURFileLazyAndUnavailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed before processing"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewCURFileProcessor(path, curFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, input := range [][]byte{curTestFile(200, 100, 24, nil), []byte{0, 0, 2, 0}, curTestFile(1, 1, 24, nil)} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("lazy or malformed fallback", err)
		}
	}
	input := curTestFile(1, 1, 24, []byte{30, 20, 10})
	if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), []byte("another format")); !errors.Is(err, ErrNotCUR) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format dispatch", err)
	}
}
func TestCURFileLimitsAndQueue(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"header", "decode", "transform"} {
		l := curFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewCURFileProcessor(path, l); p != nil || !errors.Is(err, ErrCURFileLimits) {
			t.Fatal("unbounded file processor", field, err)
		}
	}
	l := curFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewCURFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), curTestFile(200, 100, 24, nil)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue timeout", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request's slot")
	}
	<-p.slots
}

func TestCURHeaderHeightBudgetAndCancellation(t *testing.T) {
	for _, height := range []uint32{0, 1, 2, 3, 178956970, 357913940, 357913942} {
		b := curTestFile(1, 1, 24, nil)
		binary.LittleEndian.PutUint32(b[30:], height)
		h, err := ReadCURHeader(context.Background(), b, 62)
		valid := height/2 > 0 && height/2 <= 178956970
		if valid {
			if err != nil || h.Width != 1 || h.Height != int(height/2) || h.Mode != "RGB" {
				t.Fatal("post-halving pixel ceiling", height, h, err)
			}
		} else if !errors.Is(err, ErrCURHeader) || h.Width != 0 {
			t.Fatal("invalid dimensions", height, h, err)
		}
	}
	b := curTestFile(1, 1, 32, []byte{25, 50, 100, 127})
	if _, err := ReadCURHeader(context.Background(), b, 61); !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("header budget", err)
	}
	h, err := ReadCURHeader(context.Background(), b, 62)
	if err != nil || h.DataOffset != 62 || h.HeaderBytes != 62 || h.RawMode != "BGRA" {
		t.Fatal("exact header budget", h, err)
	}
	r, err := DecodeCUR(context.Background(), h, b, int64(len(b)+4))
	if err != nil {
		t.Fatal(err)
	}
	b[62] = 0
	if r.Pixels[2] != 25 {
		t.Fatal("pixels retain input alias")
	}
	dir := make([]byte, 38)
	copy(dir, []byte{0, 0, 2, 0, 2, 0})
	dir[6] = 1
	dir[7] = 1
	dir[22] = 2
	dir[23] = 2
	binary.LittleEndian.PutUint32(dir[18:], 38)
	binary.LittleEndian.PutUint32(dir[34:], 38)
	dir = append(dir, b[22:]...)
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: 3}
	if h, err := ReadCURHeader(ctx, dir, 1024); !errors.Is(err, context.Canceled) || h.Width != 0 {
		t.Fatal("directory cancellation", h, err)
	}
	if h, err := ReadCURHeader(parent, nil, 1); !errors.Is(err, context.Canceled) || h.Width != 0 {
		t.Fatal("pre-cancel", err)
	}
}
