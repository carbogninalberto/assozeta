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

func webpTestLimits() WebPFileLimits {
	l := imageProcessorTestLimits()
	return WebPFileLimits{l.Codec, l.MaxDecodeBytes, l.MaxTransformBytes}
}
func webpTestInput(n int) []byte {
	b := make([]byte, n)
	copy(b, []byte("RIFF"))
	copy(b[8:], []byte("WEBPVP8L"))
	return b
}
func webpTestFrame() []byte {
	b := make([]byte, 32)
	copy(b, "AZWP")
	binary.BigEndian.PutUint32(b[4:], 2)
	binary.BigEndian.PutUint32(b[8:], 1)
	b[12] = 3
	binary.BigEndian.PutUint32(b[16:], 1)
	binary.BigEndian.PutUint32(b[20:], 1)
	return append(b, 1, 2, 3, 4, 5, 6)
}
func TestWebPFrameProtocol(t *testing.T) {
	frame := webpTestFrame()
	h, pixels, err := decodeWebPFrame(frame, 6, true)
	if err != nil || h.Width != 2 || h.Height != 1 || h.Mode != "RGB" || h.Frames != 1 || h.Loop != 1 || !bytes.Equal(pixels, []byte{1, 2, 3, 4, 5, 6}) {
		t.Fatal("valid normalized frame", err)
	}
	if h, p, err := decodeWebPFrame(frame, 5, true); !errors.Is(err, ErrWorkingLimit) || h.Width != 0 || p != nil {
		t.Fatal("pixel output limit", err)
	}
	if h, p, err := decodeWebPFrame(frame[:32], 6, false); err != nil || h.Width != 2 || p != nil {
		t.Fatal("header frame", err)
	}
	for _, at := range []int{0, 13, 14, 15, 28, 29, 30, 31} {
		b := bytes.Clone(frame)
		b[at] ^= 1
		if h, p, err := decodeWebPFrame(b, 64, true); !errors.Is(err, ErrWebPDecode) || h.Width != 0 || p != nil {
			t.Fatal("reserved/magic accepted", at, err)
		}
	}
	for _, value := range []byte{0, 1, 2, 5, 255} {
		b := bytes.Clone(frame)
		b[12] = value
		if _, p, err := decodeWebPFrame(b, 64, true); !errors.Is(err, ErrWebPDecode) || p != nil {
			t.Fatal("bad channels", err)
		}
	}
	for _, field := range []struct {
		offset int
		value  uint32
	}{{4, 0}, {8, 0}, {4, 16777217}, {8, 16777217}, {16, 0}, {20, 65536}} {
		b := bytes.Clone(frame)
		binary.BigEndian.PutUint32(b[field.offset:], field.value)
		if _, p, err := decodeWebPFrame(b, 64, true); !errors.Is(err, ErrWebPDecode) || p != nil {
			t.Fatal("bad header", field, err)
		}
	}
	for _, b := range [][]byte{frame[:31], frame[:37], append(bytes.Clone(frame), 0)} {
		if _, p, err := decodeWebPFrame(b, 64, true); !errors.Is(err, ErrWebPDecode) || p != nil {
			t.Fatal("frame length", err)
		}
	}
	if _, p, err := decodeWebPFrame(frame, 64, false); !errors.Is(err, ErrWebPDecode) || p != nil {
		t.Fatal("pixels in info response", err)
	}
}
func TestWebPEarlyBoundsAndUnavailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed executable"), 0700); err != nil {
		t.Fatal(err)
	}
	l := webpTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewWebPFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if got, err := p.Process(context.Background(), webpTestInput(17)); !errors.Is(err, ErrWebPCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("required native parser unavailable", err)
	}
	if _, err := p.decoder.Inspect(context.Background(), []byte("not WebP")); !errors.Is(err, ErrWebPDecode) {
		t.Fatal(err)
	}
	if _, err := p.decoder.Inspect(context.Background(), make([]byte, l.Codec.MaxEncodedBytes+1)); !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("input bound", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := p.Process(ctx, webpTestInput(17)); !errors.Is(err, context.Canceled) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("pre-cancelled", err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), webpTestInput(17)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released foreign slot")
	}
	<-p.slots
	validPath, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"decode", "transform", "input", "concurrency", "timeout", "overflow"} {
		l := webpTestLimits()
		switch field {
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		case "input":
			l.Codec.MaxEncodedBytes = 0
		case "concurrency":
			l.Codec.Concurrency = 0
		case "timeout":
			l.Codec.Timeout = 0
		case "overflow":
			l.MaxDecodeBytes = int64(^uint(0) >> 1)
		}
		if p, err := NewWebPFileProcessor(validPath, l); !errors.Is(err, ErrWebPLimits) || p != nil {
			t.Fatal("unbounded constructor", field)
		}
	}
}
