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

func pngFileTestLimits() PNGFileLimits {
	return PNGFileLimits{Codec: jpegTestLimits(), MaxHeaderBytes: 8 << 20, MaxTailBytes: 8 << 20, MaxDecodeBytes: 64 << 20, MaxTransformBytes: 64 << 20}
}
func TestPNGFileLazyAdmission(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed before pixel work"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewPNGFileProcessor(path, pngFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	header := append([]byte("\x89PNG\r\n\x1a\n"), pngChunk("IHDR", pngIHDR(200, 100, 8, 2))...)
	header = append(header, []byte{0, 0, 0, 100, 'I', 'D', 'A', 'T'}...)
	got, err := p.Process(context.Background(), header)
	if err != nil || !got.KeepOriginal || got.JPEG != nil {
		t.Fatal("lazy passthrough invoked unavailable codec", err)
	}
	small := append([]byte("\x89PNG\r\n\x1a\n"), pngChunk("IHDR", pngIHDR(1, 1, 8, 0))...)
	small = append(small, pngChunk("IDAT", pngCompressed(t, []byte{0, 42}))...)
	small = append(small, pngChunk("IEND", nil)...)
	if got, err := p.Process(context.Background(), small); !errors.Is(err, ErrPNGCodecUnavailable) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("missing decoder hidden as image fallback", err)
	}
	if got, err := p.Process(context.Background(), []byte("another format")); !errors.Is(err, ErrNotPNG) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("format dispatch", err)
	}
}
func TestPNGTailStateAndBounds(t *testing.T) {
	data := append([]byte{0, 0, 0, 0}, pngChunk("tEXt", []byte("comment\x00after"))...)
	data = append(data, pngChunk("IEND", nil)...)
	initial := pngHeaderState{header: PNGHeader{PNGPixelSpec: PNGPixelSpec{Width: 3, Height: 2, Depth: 8, Color: 0}, Mode: "L", Frames: 1, Comment: []byte("before")}, sequence: -1}
	got, err := readPNGTail(context.Background(), data, initial, PNGStreamImage{Sequence: -1}, len(data))
	if err != nil || !bytes.Equal(got.header.Comment, []byte("after")) || !bytes.Equal(initial.header.Comment, []byte("before")) {
		t.Fatal("tail metadata", got, err)
	}
	exact := len(data) - 4 // IEND CRC is never read.
	for _, delta := range []int{-1, 0, 1} {
		got, err := readPNGTail(context.Background(), data, initial, PNGStreamImage{Sequence: -1}, exact+delta)
		if delta < 0 {
			if !errors.Is(err, ErrWorkingLimit) || got.header.Comment != nil {
				t.Fatal("tail budget ignored", err)
			}
		} else if err != nil {
			t.Fatal("exact tail budget rejected", err)
		}
	}
	malformed := append([]byte{0, 0, 0, 0}, []byte{0, 0, 0, 10, 'I', 'D', 'A', 'T', 1}...)
	if _, err := readPNGTail(context.Background(), malformed, initial, PNGStreamImage{Sequence: -1}, 1024); !errors.Is(err, ErrPNGHeader) {
		t.Fatal("truncated later chunk accepted", err)
	}
	// Unlike an unread later IDAT, remaining bytes of the active chunk use read,
	// not safe_read, and may be absent without failing.
	if _, err := readPNGTail(context.Background(), nil, initial, PNGStreamImage{Remaining: 1000, Sequence: -1}, 1); err != nil {
		t.Fatal("active remainder truncation", err)
	}
	for _, suffix := range [][]byte{nil, {1, 2, 3}, {0, 0, 0, 1, 'b', 'a', 'd', '!'}} {
		data := append([]byte{0, 0, 0, 0}, suffix...)
		if _, err := readPNGTail(context.Background(), data, initial, PNGStreamImage{Sequence: -1}, 1024); err != nil {
			t.Fatal("tail boundary incorrectly rejected", err)
		}
	}
	// Animated first-frame parsing stops before inspecting the next fcTL body.
	initial.header.Frames = 2
	data = append([]byte{0, 0, 0, 0}, []byte{255, 255, 255, 255, 'f', 'c', 'T', 'L'}...)
	if _, err := readPNGTail(context.Background(), data, initial, PNGStreamImage{Sequence: -1}, 12); err != nil {
		t.Fatal("next animated frame inspected", err)
	}
	initial.header.Frames = 1
	if _, err := readPNGTail(context.Background(), data, initial, PNGStreamImage{Sequence: -1}, 12); !errors.Is(err, ErrPNGHeader) {
		t.Fatal("static fcTL skipped", err)
	}
}
func TestPNGCanvasComposition(t *testing.T) {
	input := Raster{1, 1, "RGBA", []byte{1, 2, 3, 4}}
	h := PNGHeader{PNGPixelSpec: PNGPixelSpec{Width: 3, Height: 2, Depth: 8, Color: 6}, Mode: "RGBA", Tile: PNGRect{1, 1, 1, 1}}
	want := make([]byte, 24)
	copy(want[16:], input.Pixels)
	for _, delta := range []int64{-1, 0, 1} {
		got, err := pngCanvas(context.Background(), h, input, 28+delta)
		if delta < 0 {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("canvas budget ignored", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, want) {
			t.Fatal("canvas placement", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := pngCanvas(ctx, h, input, 1024); !errors.Is(err, context.Canceled) || got.Pixels != nil {
		t.Fatal("cancelled canvas", err)
	}
}
func TestPNGFileAdmissionTimeout(t *testing.T) {
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	limits := pngFileTestLimits()
	limits.Codec.Concurrency = 1
	limits.Codec.Timeout = 20 * time.Millisecond
	p, err := NewPNGFileProcessor(path, limits)
	if err != nil {
		t.Fatal(err)
	}
	p.slots <- struct{}{}
	if got, err := p.Process(context.Background(), []byte("\x89PNG\r\n\x1a\n")); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("pipeline queue timeout", err)
	}
	if len(p.slots) != 1 {
		t.Fatal("released another request slot")
	}
	<-p.slots
	for _, field := range []string{"header", "tail", "decode", "transform"} {
		l := pngFileTestLimits()
		switch field {
		case "header":
			l.MaxHeaderBytes = 0
		case "tail":
			l.MaxTailBytes = 0
		case "decode":
			l.MaxDecodeBytes = 0
		case "transform":
			l.MaxTransformBytes = 0
		}
		if p, err := NewPNGFileProcessor(path, l); p != nil || !errors.Is(err, ErrPNGStreamLimits) {
			t.Fatal("unbounded PNG file processor", field, err)
		}
	}
}

func pngIHDR(width, height int, depth, color byte) []byte {
	out := make([]byte, 13)
	binary.BigEndian.PutUint32(out, uint32(width))
	binary.BigEndian.PutUint32(out[4:], uint32(height))
	out[8], out[9] = depth, color
	return out
}

func TestPNGCanvasAdam7RetainsPillowOrigin(t *testing.T) {
	h := PNGHeader{PNGPixelSpec: PNGPixelSpec{Width: 3, Height: 2, Depth: 8, Color: 6, Interlace: true}, Mode: "RGBA", Tile: PNGRect{1, 1, 1, 1}}
	input := Raster{1, 1, "RGBA", []byte{10, 20, 30, 128}}
	want := make([]byte, 24)
	copy(want, input.Pixels)
	got, err := pngCanvas(context.Background(), h, input, 28)
	if err != nil || !bytes.Equal(got.Pixels, want) {
		t.Fatal("Adam7 first tile must retain Pillow's origin placement", got.Pixels, err)
	}
}

func TestPNGTailCumulativeMemoryAndCancellation(t *testing.T) {
	data := append([]byte{0, 0, 0, 0}, pngChunk("tEXt", []byte("comment\x00ab"))...)
	data = append(data, pngChunk("IEND", nil)...)
	for _, start := range []int64{pngTextMemoryLimit - 2, pngTextMemoryLimit - 1} {
		state := pngHeaderState{textMemory: start, sequence: -1}
		got, err := readPNGTail(context.Background(), data, state, PNGStreamImage{Sequence: -1}, len(data))
		if start == pngTextMemoryLimit-2 {
			if err != nil || !bytes.Equal(got.header.Comment, []byte("ab")) {
				t.Fatal("exact text limit", err)
			}
		} else if !errors.Is(err, ErrPNGHeader) || got.header.Comment != nil {
			t.Fatal("header text memory lost", err)
		}
	}
	many := append([]byte{0, 0, 0, 0}, bytes.Repeat(pngChunk("abCD", nil), 1000)...)
	for _, trigger := range []int{2, 10, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		got, err := readPNGTail(ctx, many, pngHeaderState{}, PNGStreamImage{Sequence: -1}, len(many))
		cancel()
		if !errors.Is(err, context.Canceled) || got.header.Comment != nil {
			t.Fatal("tail cancellation exposed state", err)
		}
	}
	h := PNGHeader{PNGPixelSpec: PNGPixelSpec{Width: 1024, Height: 128, Depth: 8}, Mode: "L", Tile: PNGRect{1, 1, 1023, 127}}
	input := Raster{1023, 127, "L", make([]byte, 1023*127)}
	for _, trigger := range []int{2, 10, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		got, err := pngCanvas(ctx, h, input, 1<<20)
		cancel()
		if !errors.Is(err, context.Canceled) || got.Pixels != nil {
			t.Fatal("canvas cancellation exposed pixels", err)
		}
	}
}
