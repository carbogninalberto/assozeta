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

func TestPCDAgainstPillow(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_PCD_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name               string
		Data, Pixels, JPEG []byte
		Header             *PCDHeader
		Keep               bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) != 59 {
		t.Fatal("required PCD cases", len(cases), err)
	}
	l := bmpFileTestLimits()
	l.Codec.MaxEncodedBytes = 4 << 20
	l.Codec.MaxRasterBytes = 4 << 20
	l.Codec.Timeout = 30 * time.Second
	p, err := NewPCDFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), l)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		c := cases[i]
		before := bytes.Clone(c.Data)
		if pcdSignature(c.Data) {
			got, err := p.Process(context.Background(), c.Data)
			if err != nil || got.KeepOriginal != c.Keep || !bytes.Equal(got.JPEG, c.JPEG) {
				t.Errorf("%s: file mismatch keep=%v err=%v", c.Name, got.KeepOriginal, err)
			}
		}
		h, err := ReadPCDHeader(context.Background(), c.Data, l.MaxHeaderBytes)
		if c.Header == nil {
			if err == nil {
				t.Errorf("%s: invalid header accepted", c.Name)
			}
			return
		}
		if err != nil || h != *c.Header {
			t.Errorf("%s: header %+v want %+v err=%v", c.Name, h, *c.Header, err)
			return
		}
		r, err := DecodePCD(context.Background(), h, c.Data, l.MaxDecodeBytes)
		if c.Pixels == nil {
			if err == nil || r.Pixels != nil {
				t.Errorf("%s: invalid pixels accepted", c.Name)
			}
		} else if err != nil || r.Width != 768 || r.Height != 512 || r.Mode != "RGB" || !bytes.Equal(r.Pixels, c.Pixels) {
			t.Errorf("%s: pixel mismatch %v", c.Name, err)
		}
		if !bytes.Equal(c.Data, before) {
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
		t.Fatal("retained PCD admission")
	}
}

func TestPCDFileStagesAndNativeCancellation(t *testing.T) {
	b := pcdTestFile()
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	const pixels = 768 * 512 * 3
	for _, stage := range []string{"input", "header", "decode", "transform", "raster"} {
		l := pcdFileTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(b) - 1
		case "header":
			l.MaxHeaderBytes = 2047
		case "decode":
			l.MaxDecodeBytes = int64(len(b)+pixels) - 1
		case "transform":
			l.MaxTransformBytes = 2*pixels - 1
		case "raster":
			l.Codec.MaxRasterBytes = pixels - 1
		}
		p, err := NewPCDFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := p.Process(context.Background(), b); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage budget hidden", stage, err)
		}
	}
	l := pcdFileTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 5 * time.Second
	p, err := NewPCDFileProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	baseline, err := p.Process(context.Background(), b)
	if err != nil || baseline.KeepOriginal || len(baseline.JPEG) == 0 {
		t.Fatal("baseline", err)
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	p.encoder, err = NewJPEGEncoder(fault, l.Codec)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	t.Setenv("ASSOZETA_TEST_SECRET", "must-not-reach-child")
	ctx, cancel := context.WithCancel(context.Background())
	pending := make(chan error, 1)
	defer func() {
		cancel()
		if pending != nil {
			select {
			case <-pending:
			case <-time.After(2 * time.Second):
				t.Error("child cleanup did not finish")
			}
		}
	}()
	go func() {
		got, err := p.Process(ctx, b)
		if got.KeepOriginal || got.JPEG != nil {
			pending <- errors.New("cancelled pipeline exposed output")
			return
		}
		pending <- err
	}()
	deadline := time.Now().Add(2 * time.Second)
	for {
		marker, err := os.ReadFile(fault + ".started")
		if err == nil && string(marker) == "1" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("encoder did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	got, err := p.Process(queued, b)
	stop()
	if !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("queued call escaped admission", err)
	}
	if marker, err := os.ReadFile(fault + ".started"); err != nil || string(marker) != "1" {
		t.Fatal("queued call spawned a child")
	}
	cancel()
	select {
	case err := <-pending:
		pending = nil
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("encoder not reaped")
	}
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("cancel retained admission")
	}
	p.encoder, err = NewJPEGEncoder(path, l.Codec)
	if err != nil {
		t.Fatal(err)
	}
	got, err = p.Process(context.Background(), b)
	if err != nil || got.KeepOriginal || !bytes.Equal(got.JPEG, baseline.JPEG) {
		t.Fatal("pipeline not reusable", err)
	}
	p.encoder, err = NewJPEGEncoder(fault, l.Codec)
	if err != nil {
		t.Fatal(err)
	}
	p.limits.Codec.Timeout = 20 * time.Millisecond
	got, err = p.Process(context.Background(), b)
	if !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("whole-pipeline deadline", err)
	}
	if len(p.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("deadline retained admission")
	}
}
