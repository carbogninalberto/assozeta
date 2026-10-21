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

func TestMSPAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_MSP_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name               string
		Data, Pixels, JPEG []byte
		Header             *MSPHeader
		Lazy, Keep         bool
		PixelMode          string
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 1704 {
		t.Fatal("MSP fixture", len(cases), err)
	}
	l := mspFileTestLimits()
	l.Codec.MaxEncodedBytes = 4 << 20
	l.Codec.Timeout = 30 * time.Second
	l.MaxDecodeBytes = 512 << 20
	p, err := NewMSPFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), l)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := cases[i]
		before := bytes.Clone(tc.Data)
		if mspSignature(tc.Data) {
			got, err := p.Process(context.Background(), tc.Data)
			if err != nil || got.KeepOriginal != tc.Keep || !bytes.Equal(got.JPEG, tc.JPEG) {
				t.Errorf("%s: file difference keep=%v err=%v", tc.Name, got.KeepOriginal, err)
			}
		}
		h, err := ReadMSPHeader(context.Background(), tc.Data, 4<<20)
		if tc.Header == nil {
			if !errors.Is(err, ErrMSPHeader) || h.Width != 0 {
				t.Errorf("%s: invalid header accepted: %v", tc.Name, err)
			}
			return
		}
		if err != nil || h != *tc.Header {
			t.Errorf("%s: header %+v want %+v: %v", tc.Name, h, *tc.Header, err)
			return
		}
		if !tc.Lazy {
			got, err := DecodeMSP(context.Background(), h, tc.Data, 512<<20)
			if tc.Pixels == nil {
				if !errors.Is(err, ErrMSPDecode) || got.Pixels != nil {
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
		go func() { defer wg.Done(); check((i * 73) % len(cases)) }()
	}
	wg.Wait()
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("pipeline retained admission")
	}
}

func TestMSPFileStageBudgetsAndCancellation(t *testing.T) {
	input := mspTestFile(1, 1, []byte{255})
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	for _, stage := range []string{"input", "header", "decode", "transform", "encode"} {
		l := mspFileTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(input) - 1
		case "header":
			l.MaxHeaderBytes = 31
		case "decode":
			l.MaxDecodeBytes = int64(len(input))
		case "transform":
			l.MaxTransformBytes = 1
		case "encode":
			l.Codec.MaxEncodedBytes = 200
		}
		p, err := NewMSPFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage limit hidden", stage, err)
		}
	}
	l := mspFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 5 * time.Second
	p, err := NewMSPFileProcessor(path, l)
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
	got, err := p.Process(queued, mspTestFile(200, 100, nil))
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
