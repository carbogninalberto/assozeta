//go:build imageintegration

package loginimage

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"sync"
	"testing"
	"time"
)

func TestTGAAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_TGA_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name               string
		Data, Pixels, JPEG []byte
		Header             *TGAHeader
		Lazy, Keep         bool
		PixelMode          string
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 1112 {
		t.Fatal("TGA fixture", len(cases), err)
	}
	l := tgaFileTestLimits()
	l.Codec.MaxEncodedBytes = 4 << 20
	l.Codec.Timeout = 30 * time.Second
	l.MaxDecodeBytes = 512 << 20
	p, err := NewTGAFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), l)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := cases[i]
		before := bytes.Clone(tc.Data)
		if tgaSignature(tc.Data) {
			got, err := p.Process(context.Background(), tc.Data)
			if err != nil || got.KeepOriginal != tc.Keep || !bytes.Equal(got.JPEG, tc.JPEG) {
				t.Errorf("%s: file difference keep=%v err=%v", tc.Name, got.KeepOriginal, err)
			}
		}
		h, err := ReadTGAHeader(context.Background(), tc.Data, 4<<20)
		if tc.Header == nil {
			if !errors.Is(err, ErrTGAHeader) || h.Width != 0 {
				t.Errorf("%s: invalid header accepted: %v", tc.Name, err)
			}
			return
		}
		if err != nil || h != *tc.Header {
			t.Errorf("%s: header %+v want %+v: %v", tc.Name, h, *tc.Header, err)
			return
		}
		if !tc.Lazy {
			got, err := DecodeTGA(context.Background(), h, tc.Data, 512<<20)
			if tc.Pixels == nil {
				if !errors.Is(err, ErrTGADecode) || got.Pixels != nil {
					t.Errorf("%s: invalid pixels accepted: %v", tc.Name, err)
				}
			} else if err != nil || got.Mode != tc.PixelMode || got.Width != h.Width || got.Height != h.Height || !bytes.Equal(got.Pixels, tc.Pixels) {
				t.Errorf("%s: pixels differ: %v", tc.Name, err)
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
		go func() { defer wg.Done(); check(0) }()
	}
	wg.Wait()
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("pipeline retained admission")
	}
}

func TestTGAFileStageBudgetsAndCancellation(t *testing.T) {
	input := tgaTestFile(1, 1, 3, 8, 32, []byte{42})
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	for _, stage := range []string{"input", "header", "decode", "transform", "encode"} {
		l := tgaFileTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(input) - 1
		case "header":
			l.MaxHeaderBytes = 17
		case "decode":
			l.MaxDecodeBytes = int64(len(input))
		case "transform":
			l.MaxTransformBytes = 1
		case "encode":
			l.Codec.MaxEncodedBytes = 200
		}
		p, err := NewTGAFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage limit hidden", stage, err)
		}
	}
	l := tgaFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 5 * time.Second
	p, err := NewTGAFileProcessor(path, l)
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
	got, err := p.Process(queued, tgaTestFile(200, 100, 3, 8, 32, nil))
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
