//go:build imageintegration

package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"os"
	"sync"
	"testing"
	"time"
)

func TestBMPAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_BMP_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name               string
		Data, Pixels, JPEG []byte
		Header             *BMPHeader
		Lazy, Keep         bool
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 534 {
		t.Fatal("BMP fixture", len(cases), err)
	}
	limits := bmpFileTestLimits()
	limits.Codec.MaxEncodedBytes = 4 << 20
	limits.Codec.Timeout = 30 * time.Second
	limits.MaxDecodeBytes = 512 << 20
	p, err := NewBMPFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := cases[i]
		before := bytes.Clone(tc.Data)
		if bmpSignature(tc.Data) {
			got, err := p.Process(context.Background(), tc.Data)
			if err != nil || got.KeepOriginal != tc.Keep || !bytes.Equal(got.JPEG, tc.JPEG) {
				t.Errorf("%s: whole-file difference: keep=%v err=%v", tc.Name, got.KeepOriginal, err)
			}
		}
		h, err := ReadBMPHeader(context.Background(), tc.Data, 1<<20)
		if tc.Header == nil {
			if !errors.Is(err, ErrBMPHeader) || h.Width != 0 {
				t.Errorf("%s: header accepted: %v", tc.Name, err)
			}
			return
		}
		want := tc.Header
		if err != nil || h.Width != want.Width || h.Height != want.Height || h.Mode != want.Mode || h.RawMode != want.RawMode || h.DataOffset != want.DataOffset || h.HeaderBytes != want.HeaderBytes || h.Format != want.Format || h.TopDown != want.TopDown || h.PixelWarning != want.PixelWarning {
			t.Errorf("%s: header %+v want %+v: %v", tc.Name, h, *want, err)
			return
		}
		if !tc.Lazy {
			got, err := DecodeBMP(context.Background(), h, tc.Data, 512<<20)
			if tc.Pixels == nil {
				if !errors.Is(err, ErrBMPDecode) || got.Pixels != nil {
					t.Errorf("%s: pixels accepted: %v", tc.Name, err)
				}
			} else if err != nil || got.Mode != h.Mode || got.Width != h.Width || got.Height != h.Height || !bytes.Equal(got.Pixels, tc.Pixels) {
				t.Errorf("%s: pixel difference: %v", tc.Name, err)
			}
		}
		if !bytes.Equal(tc.Data, before) {
			t.Error("input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(130) }()
	}
	wg.Wait()
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("pipeline retained admission")
	}
}

func TestBMPFileStageBudgetsAndCancellation(t *testing.T) {
	input := bmpTestFile(1, 1, 8, []byte{0, 0, 0, 0, 42})
	binary.LittleEndian.PutUint32(input[10:], 58)
	binary.LittleEndian.PutUint32(input[46:], 1)
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	for _, stage := range []string{"input", "header", "decode", "transform", "encode"} {
		l := bmpFileTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(input) - 1
		case "header":
			l.MaxHeaderBytes = 53
		case "decode":
			l.MaxDecodeBytes = int64(len(input))
		case "transform":
			l.MaxTransformBytes = 1
		case "encode":
			l.Codec.MaxEncodedBytes = 100
		}
		p, err := NewBMPFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage limit hidden", stage, err)
		}
	}
	l := bmpFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 5 * time.Second
	p, err := NewBMPFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	p.encoder, err = NewJPEGEncoder(fault, l.Codec)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		got, err := p.Process(ctx, input)
		if got.KeepOriginal || got.JPEG != nil {
			done <- errors.New("cancelled pipeline exposed result")
			return
		}
		done <- err
	}()
	deadline := time.Now().Add(2 * time.Second)
	for {
		if b, err := os.ReadFile(fault + ".started"); err == nil && len(b) > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("encoder did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	got, err := p.Process(queued, bmpTestFile(200, 100, 24, nil))
	stop()
	if !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("whole pipeline admission", err)
	}
	if b, err := os.ReadFile(fault + ".started"); err != nil || string(b) != "1" {
		t.Fatal("queued request spawned a child")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("encoder was not reaped")
	}
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("cancelled pipeline retained admission")
	}
	p.encoder, err = NewJPEGEncoder(path, l.Codec)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := p.Process(context.Background(), input); err != nil || got.KeepOriginal || len(got.JPEG) == 0 {
		t.Fatal("pipeline not reusable", err)
	}
}
