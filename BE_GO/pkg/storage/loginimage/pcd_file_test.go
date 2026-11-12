package loginimage

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func pcdTestFile() []byte {
	b := make([]byte, 786432)
	copy(b[2048:], "PCD_")
	for pos := 196608; pos < len(b); pos += 2304 {
		for i := 0; i < 1536; i++ {
			b[pos+i] = 73
		}
		for i := 1536; i < 1920; i++ {
			b[pos+i] = 156
		}
		for i := 1920; i < 2304; i++ {
			b[pos+i] = 137
		}
	}
	return b
}
func pcdFileTestLimits() PCDFileLimits {
	l := bmpFileTestLimits()
	l.Codec.MaxRasterBytes = 4 << 20
	l.Codec.MaxEncodedBytes = 4 << 20
	return l
}
func TestPCDHeaderPixelsAndBudgets(t *testing.T) {
	b := pcdTestFile()
	ctx := context.Background()
	for _, n := range []int{2048, 3586, 3587, 4095, 4096} {
		h, err := ReadPCDHeader(ctx, b[:n], 2048)
		if n < 3587 {
			if !errors.Is(err, ErrPCDHeader) || h.Width != 0 {
				t.Fatal("truncated header", n, h, err)
			}
		} else if err != nil || h != (PCDHeader{768, 512, 0}) {
			t.Fatal("header", n, h, err)
		}
	}
	if _, err := ReadPCDHeader(ctx, b, 1539); err != nil {
		t.Fatal("exact header budget", err)
	}
	for _, limit := range []int{0, 1538} {
		if _, err := ReadPCDHeader(ctx, b, limit); !errors.Is(err, ErrWorkingLimit) {
			t.Fatal("header budget", limit, err)
		}
	}
	h, err := ReadPCDHeader(ctx, b, 2048)
	if err != nil {
		t.Fatal(err)
	}
	budget := int64(len(b) + 768*512*3)
	if r, err := DecodePCD(ctx, h, b, budget-1); !errors.Is(err, ErrWorkingLimit) || r.Pixels != nil {
		t.Fatal("decode budget", err)
	}
	r, err := DecodePCD(ctx, h, b, budget)
	if err != nil || r.Width != 768 || r.Height != 512 || r.Mode != "RGB" || !bytes.Equal(r.Pixels, bytes.Repeat([]byte{99}, 768*512*3)) {
		t.Fatal("exact raster/budget", err)
	}
	b[196608] = 0
	if r.Pixels[0] != 99 {
		t.Fatal("pixels retain input alias")
	}
	for _, bad := range []PCDHeader{{767, 512, 0}, {768, 511, 0}, {768, 512, 4}, {768, 512, 1}, {512, 768, 0}, {512, 768, 2}, {}} {
		if r, err := DecodePCD(ctx, bad, b, budget); !errors.Is(err, ErrPCDDecode) || r.Pixels != nil {
			t.Fatal("forged header", bad, err)
		}
	}
	for _, orientation := range []uint8{1, 3} {
		b[3586] = orientation
		h, err := ReadPCDHeader(ctx, b, 1539)
		if err != nil || h != (PCDHeader{512, 768, orientation}) {
			t.Fatal("rotated header", h, err)
		}
		r, err := DecodePCD(ctx, h, b, budget)
		if err != nil || r.Width != 512 || r.Height != 768 || r.Mode != "RGB" {
			t.Fatal("rotated raster", orientation, err)
		}
		// The sole black source pixel at (0,0) moves to opposite corners.
		black := 767 * 512 * 3
		if orientation == 3 {
			black = 511 * 3
		}
		want := bytes.Repeat([]byte{99}, 768*512*3)
		copy(want[black:black+3], []byte{0, 0, 0})
		if !bytes.Equal(r.Pixels, want) {
			t.Fatal("rotation direction", orientation)
		}
		if got, err := DecodePCD(ctx, h, b, budget-1); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
			t.Fatal("rotated decode budget", orientation, err)
		}
	}
	parent, cancel := context.WithCancel(ctx)
	defer cancel()
	controlled := &cancellingImageContext{Context: parent, cancel: cancel, trigger: 3}
	if r, err := DecodePCD(controlled, h, b, budget); !errors.Is(err, context.Canceled) || r.Pixels != nil {
		t.Fatal("pixel cancellation", err)
	}
	if _, err := ReadPCDHeader(parent, b, 2048); !errors.Is(err, context.Canceled) {
		t.Fatal("pre-cancelled header", err)
	}
}
func TestPCDFileFallbackUnavailableAndLimits(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed executable"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewPCDFileProcessor(path, pcdFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	b := pcdTestFile()
	for _, input := range [][]byte{b[:2052], b[:3587]} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("malformed fallback required encoder", err)
		}
	}
	b[3586] = 1
	if got, err := p.Process(context.Background(), b); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("rotated image hid missing encoder", err)
	}
	b[3586] = 0
	if got, err := p.Process(context.Background(), b); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), []byte("other")); !errors.Is(err, ErrNotPCD) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format selection", err)
	}
	path, err = os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"header", "decode", "transform"} {
		l := pcdFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewPCDFileProcessor(path, l); p != nil || !errors.Is(err, ErrPCDFileLimits) {
			t.Fatal("unbounded constructor", field, err)
		}
	}
	l := pcdFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err = NewPCDFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), b); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue deadline", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request's slot")
	}
	<-p.slots
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := p.Process(ctx, b); !errors.Is(err, context.Canceled) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("pre-cancelled pipeline", err)
	}
}
