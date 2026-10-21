package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"testing"
	"time"
)

func TestPNGStreamFrameValidation(t *testing.T) {
	spec := PNGStreamSpec{PNGPixelSpec: PNGPixelSpec{Width: 3, Height: 2, Depth: 8}, DataOffset: 33, DataLength: 12, Sequence: -1}
	frame := make([]byte, 44)
	copy(frame, "AZPN")
	binary.BigEndian.PutUint64(frame[8:16], 45)
	binary.BigEndian.PutUint64(frame[24:32], ^uint64(0))
	binary.BigEndian.PutUint64(frame[32:40], 4)
	copy(frame[40:], []byte{0, 1, 2, 3})
	got, err := decodePNGStreamFrame(context.Background(), spec, frame, 45, 1024)
	if err != nil || got.Position != 45 || got.Remaining != 0 || got.Sequence != -1 || !bytes.Equal(got.Pixels, []byte{1, 2, 3, 0, 0, 0}) {
		t.Fatal("early complete row frame", got, err)
	}
	for _, index := range []int{0, 4, 7, 8, 16, 24, 32, 40} {
		bad := bytes.Clone(frame)
		bad[index] ^= 255
		if got, err := decodePNGStreamFrame(context.Background(), spec, bad, 45, 1024); err == nil || got.Pixels != nil {
			t.Fatal("invalid frame accepted", index, err)
		}
	}
	for _, n := range []int{0, 39, 40, 43} {
		if got, err := decodePNGStreamFrame(context.Background(), spec, frame[:n], 45, 1024); err == nil || got.Pixels != nil {
			t.Fatal("short frame accepted", n, err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := decodePNGStreamFrame(ctx, spec, frame, 45, 1024); !errors.Is(err, context.Canceled) || got.Pixels != nil {
		t.Fatal("cancelled frame exposed pixels", err)
	}
}

func TestPNGStreamConstructor(t *testing.T) {
	limits := PNGStreamLimits{MaxInputBytes: 1024, MaxWorkingBytes: 1024, Concurrency: 1, Timeout: time.Second}
	if _, err := NewPNGStreamDecoder("relative", limits); !errors.Is(err, ErrPNGCodecUnavailable) {
		t.Fatal(err)
	}
	limits.MaxWorkingBytes = 0
	if _, err := NewPNGStreamDecoder("relative", limits); !errors.Is(err, ErrPNGStreamLimits) {
		t.Fatal(err)
	}
}

func TestPNGStreamUnpackCancellationAndOverflow(t *testing.T) {
	raw := make([]byte, 1025*128)
	frame := make([]byte, 40+len(raw))
	copy(frame, "AZPN")
	binary.BigEndian.PutUint64(frame[24:32], ^uint64(0))
	binary.BigEndian.PutUint64(frame[32:40], uint64(len(raw)))
	spec := PNGStreamSpec{PNGPixelSpec: PNGPixelSpec{Width: 1024, Height: 128, Depth: 8}, Sequence: -1}
	for _, trigger := range []int{2, 10, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		got, err := decodePNGStreamFrame(ctx, spec, frame, 0, 1<<20)
		cancel()
		if !errors.Is(err, context.Canceled) || got.Pixels != nil {
			t.Fatal("unpack cancellation lost", err)
		}
	}
	for _, budget := range []int64{0, -1, -1 << 63} {
		got, err := decodePNGStreamFrame(context.Background(), spec, frame, 0, budget)
		if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
			t.Fatal("invalid budget accepted", err)
		}
	}
	for _, spec := range []PNGPixelSpec{{Width: 1, Height: 1, Depth: 3}, {Width: 0, Height: 1, Depth: 8}, {Width: int(^uint(0) >> 1), Height: 1, Depth: 16, Color: 6}} {
		if _, _, _, _, _, _, err := pngStreamLayout(spec); err == nil {
			t.Fatal("invalid or overflowing layout accepted")
		}
	}
}
