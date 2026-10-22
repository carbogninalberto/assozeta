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

func TestCURAgainstPillow(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_CUR_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Opening      string
		Data, Pixels, JPEG []byte
		Header             *struct {
			Width, Height int
			Mode          string
		}
		Keep bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) != 495 {
		t.Fatal("required CUR fixtures", len(cases), err)
	}
	l := bmpFileTestLimits()
	l.Codec.MaxEncodedBytes = 4 << 20
	p, err := NewCURFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), l)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		c := cases[i]
		before := bytes.Clone(c.Data)
		if curSignature(c.Data) {
			got, err := p.Process(context.Background(), c.Data)
			if err != nil || got.KeepOriginal != c.Keep || !bytes.Equal(got.JPEG, c.JPEG) {
				t.Errorf("%s: file mismatch keep=%v err=%v", c.Name, got.KeepOriginal, err)
			}
		}
		h, err := ReadCURHeader(context.Background(), c.Data, l.MaxHeaderBytes)
		if c.Header == nil {
			want := ErrCURRejected
			if c.Opening == "terminal" {
				want = ErrCURTerminal
			}
			if !errors.Is(err, want) {
				t.Errorf("%s: opening classification %v want %v", c.Name, err, want)
			}
			if err == nil {
				t.Errorf("%s: invalid header accepted", c.Name)
			}
			return
		}
		if err != nil || h.Width != c.Header.Width || h.Height != c.Header.Height || h.Mode != c.Header.Mode {
			t.Errorf("%s: header %+v want %+v err=%v", c.Name, h, c.Header, err)
			return
		}
		r, err := DecodeBMP(context.Background(), h, c.Data, l.MaxDecodeBytes)
		if c.Pixels == nil {
			if err == nil || r.Pixels != nil {
				t.Errorf("%s: invalid pixels accepted", c.Name)
			}
		} else if err != nil || r.Width != h.Width || r.Height != h.Height || r.Mode != h.Mode || !bytes.Equal(r.Pixels, c.Pixels) {
			t.Errorf("%s: pixels mismatch err=%v", c.Name, err)
		}
		if !bytes.Equal(before, c.Data) {
			t.Error("input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := range 32 {
		wg.Add(1)
		go func() { defer wg.Done(); check(i) }()
	}
	wg.Wait()
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("CUR admission retained")
	}
}

func TestCURFileStageBudgetsAndCancellation(t *testing.T) {
	input := curTestFile(1, 1, 8, []byte{0, 0, 0, 0, 42})
	binary.LittleEndian.PutUint32(input[54:], 1)
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	for _, stage := range []string{"input", "header", "decode", "transform", "encode"} {
		l := curFileTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(input) - 1
		case "header":
			l.MaxHeaderBytes = 65
		case "decode":
			l.MaxDecodeBytes = int64(len(input))
		case "transform":
			l.MaxTransformBytes = 1
		case "encode":
			l.Codec.MaxEncodedBytes = 100
		}
		p, err := NewCURFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage limit hidden", stage, err)
		}
	}
	l := curFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 5 * time.Second
	p, err := NewCURFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	p.encoder, err = NewJPEGEncoder(fault, l.Codec)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
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
	got, err := p.Process(queued, curTestFile(200, 100, 24, nil))
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
