package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"image"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func icoFileTestLimits() ICOFileLimits {
	l := imageProcessorTestLimits()
	return ICOFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxTailBytes: l.MaxTailBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes}
}

func icoTestFile(w, h, bpp int, frame []byte) []byte {
	b := make([]byte, 22)
	copy(b, []byte{0, 0, 1, 0, 1, 0})
	b[6], b[7] = byte(w), byte(h)
	binary.LittleEndian.PutUint16(b[12:], uint16(bpp))
	binary.LittleEndian.PutUint32(b[14:], uint32(len(frame)))
	binary.LittleEndian.PutUint32(b[18:], 22)
	return append(b, frame...)
}
func icoTestDIB(w, h int) []byte {
	return icoTestFile(w, h, 32, bmpTestFile(w, 2*h, 32, bytes.Repeat([]byte{25, 50, 100, 127}, w*h))[14:])
}
func icoTestPNG(t *testing.T, w, h int) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := png.Encode(&buf, image.NewGray(image.Rect(0, 0, w, h))); err != nil {
		t.Fatal(err)
	}
	return icoTestFile(w, h, 32, buf.Bytes())
}
func TestICOStageBoundsAndOwnership(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	input := icoTestDIB(1, 1)
	before := bytes.Clone(input)
	for _, delta := range []int64{0, -1} {
		l := icoFileTestLimits()
		l.MaxDecodeBytes = int64(len(input)) + 7 + delta
		p, err := NewICOFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		got, err := p.decode(context.Background(), input)
		if delta == 0 {
			if err != nil || !bytes.Equal(got.Pixels, []byte{100, 50, 25, 127}) {
				t.Fatal("exact decode budget", err)
			}
			got.Pixels[0] = 0
		} else if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
			t.Fatal("decode budget hidden", err)
		}
	}
	if !bytes.Equal(before, input) {
		t.Fatal("output aliases input")
	}
	for _, budget := range []int{61, 62} {
		l := icoFileTestLimits()
		l.MaxHeaderBytes = budget
		p, err := NewICOFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		got, err := p.decode(context.Background(), input)
		if budget == 62 {
			if err != nil || got.Pixels == nil {
				t.Fatal("exact cumulative header budget", err)
			}
		} else if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
			t.Fatal("cumulative header bound", err)
		}
	}
	l := icoFileTestLimits()
	l.MaxDecodeBytes = 1024
	p, err := NewICOFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := p.Process(context.Background(), icoTestDIB(200, 100)); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("lazy policy bypassed eager decode budget", err)
	}
	for _, field := range []string{"header", "tail", "decode", "transform", "input", "concurrency", "timeout"} {
		l := icoFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "tail":
			l.MaxTailBytes = 0
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
		}
		if p, err := NewICOFileProcessor(path, l); p != nil || err == nil {
			t.Fatal("invalid limits", field)
		}
	}
}
func TestICOUnavailableCodecAndQueue(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed executable"), 0700); err != nil {
		t.Fatal(err)
	}
	l := icoFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewICOFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for _, input := range [][]byte{[]byte{0, 0, 1, 0}, icoTestDIB(200, 100)} {
		if got, err := p.Process(context.Background(), input); err != nil || !got.KeepOriginal || got.JPEG != nil {
			t.Fatal("DIB lazy or malformed fallback", err)
		}
	}
	if got, err := p.Process(context.Background(), icoTestDIB(1, 1)); !errors.Is(err, ErrJPEGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing JPEG encoder hidden", err)
	}
	if got, err := p.Process(context.Background(), icoTestPNG(t, 200, 100)); !errors.Is(err, ErrPNGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("lazy PNG avoided required decode", err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), icoTestDIB(1, 1)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queue deadline", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released foreign slot")
	}
	<-p.slots
}
func TestICOMidOperationCancellation(t *testing.T) {
	b := bytes.Repeat(make([]byte, 16), 1000)
	b = append([]byte{0, 0, 1, 0, 232, 3}, b...)
	for _, trigger := range []int{2, 10, 500} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if got, err := readICODirectory(ctx, b, len(b)); !errors.Is(err, context.Canceled) || got.offset != 0 || got.consumed != 0 {
			t.Fatal("directory cancellation", err)
		}
		cancel()
	}
	input := icoTestDIB(8192, 16)
	entry, err := readICODirectory(context.Background(), input, len(input))
	if err != nil {
		t.Fatal(err)
	}
	h, err := ReadBMPHeader(context.Background(), input[22:], 40)
	if err != nil {
		t.Fatal(err)
	}
	h.Height /= 2
	r, err := DecodeBMP(context.Background(), h, input[22:], 8<<20)
	if err != nil {
		t.Fatal(err)
	}
	for _, trigger := range []int{2, 10, 30} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		if got, err := icoAlpha(ctx, input, entry, h, r, 8<<20); !errors.Is(err, context.Canceled) || got.Pixels != nil {
			t.Fatal("alpha cancellation exposed pixels", err)
		}
		cancel()
	}
}
