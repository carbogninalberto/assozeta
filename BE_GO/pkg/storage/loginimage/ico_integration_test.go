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

func TestICOAgainstPillow(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_ICO_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Mode         string
		Data, Pixels, JPEG []byte
		Width, Height      int
		Invalid, Keep      bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) != 678 {
		t.Fatal("required ICO fixture", len(cases), err)
	}
	p, err := NewICOFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), icoFileTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		c := cases[i]
		before := bytes.Clone(c.Data)
		if icoSignature(c.Data) {
			got, err := p.Process(context.Background(), c.Data)
			if err != nil || got.KeepOriginal != c.Keep || !bytes.Equal(got.JPEG, c.JPEG) {
				t.Errorf("%s: file mismatch keep=%v err=%v", c.Name, got.KeepOriginal, err)
			}
		}
		got, err := p.decode(context.Background(), c.Data)
		if c.Invalid {
			if err == nil || got.Pixels != nil {
				t.Errorf("%s: invalid image accepted", c.Name)
			}
		} else if err != nil || got.Mode != c.Mode || got.Width != c.Width || got.Height != c.Height || !bytes.Equal(got.Pixels, c.Pixels) {
			t.Errorf("%s: pixel mismatch mode=%s err=%v", c.Name, got.Mode, err)
		}
		if !bytes.Equal(before, c.Data) {
			t.Error("input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(i % len(cases)) }()
	}
	wg.Wait()
	if len(p.slots) != 0 {
		t.Fatal("ICO admission retained")
	}
}

func TestICOFileBudgetsAndNativeCancellation(t *testing.T) {
	input := icoTestDIB(1, 1)
	for _, stage := range []string{"input", "transform", "encode"} {
		l := icoFileTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(input) - 1
		case "transform":
			l.MaxTransformBytes = 1
		case "encode":
			l.Codec.MaxEncodedBytes = 200
		}
		p, err := NewICOFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), l)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := p.Process(context.Background(), input); !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage budget hidden", stage, err)
		}
	}
	for _, input := range [][]byte{input, icoTestPNG(t, 1, 1)} {
		fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
		if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
			t.Fatal(err)
		}
		l := icoFileTestLimits()
		l.Codec.Concurrency = 1
		p, err := NewICOFileProcessor(fault, l)
		if err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		done := make(chan error, 1)
		go func() {
			got, err := p.Process(ctx, input)
			if got.KeepOriginal || got.JPEG != nil {
				done <- errors.New("cancelled pipeline exposed output")
				return
			}
			done <- err
		}()
		deadline := time.Now().Add(2 * time.Second)
		for {
			b, _ := os.ReadFile(fault + ".started")
			if len(b) > 0 {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("native child did not start")
			}
			time.Sleep(5 * time.Millisecond)
		}
		queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
		got, err := p.Process(queued, input)
		stop()
		if !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("ICO queue bypassed", err)
		}
		cancel()
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("child not reaped")
		}
		if len(p.slots) != 0 {
			t.Fatal("slot retained")
		}
		if got, err := p.Process(context.Background(), icoTestDIB(200, 100)); err != nil || !got.KeepOriginal {
			t.Fatal("reuse after cancellation", err)
		}
	}
}
