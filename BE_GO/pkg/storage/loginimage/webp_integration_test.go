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

func TestWebPAgainstPillow(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_WEBP_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name               string
		Data, Pixels, JPEG []byte
		Header             *WebPHeader
		Keep               bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) != 178 {
		t.Fatal("required WebP fixture", len(cases), err)
	}
	l := imageProcessorTestLimits()
	p, err := NewWebPFileProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), WebPFileLimits{Codec: l.Codec, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		c := cases[i]
		before := bytes.Clone(c.Data)
		if webpSignature(c.Data) {
			got, err := p.Process(context.Background(), c.Data)
			if err != nil || got.KeepOriginal != c.Keep || !bytes.Equal(got.JPEG, c.JPEG) {
				t.Errorf("%s: file mismatch keep=%v err=%v", c.Name, got.KeepOriginal, err)
			}
		}
		h, err := p.decoder.Inspect(context.Background(), c.Data)
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
		got, err := p.decoder.Decode(context.Background(), c.Data)
		if c.Pixels == nil {
			if err == nil || got.Pixels != nil {
				t.Errorf("%s: invalid pixels accepted", c.Name)
			}
		} else if err != nil || got.Width != h.Width || got.Height != h.Height || got.Mode != h.Mode || !bytes.Equal(got.Pixels, c.Pixels) {
			t.Errorf("%s: pixel mismatch err=%v", c.Name, err)
		}
		if !bytes.Equal(c.Data, before) {
			t.Error("input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(i) }()
	}
	wg.Wait()
	if len(p.slots) != 0 || len(p.decoder.process.slots) != 0 || len(p.encoder.slots) != 0 {
		t.Fatal("WebP admission retained")
	}
}

func TestWebPNativeFailureAndAdmission(t *testing.T) {
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	d, err := NewWebPDecoder(fault, WebPLimits{1024, 512, 1, 5 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("ASSOZETA_TEST_SECRET", "must-not-reach-child")
	for _, pixels := range []bool{false, true} {
		for _, c := range []struct {
			n    int
			want error
		}{{18, ErrWorkingLimit}, {19, ErrWorkingLimit}, {20, ErrWebPDecode}, {21, ErrWebPDecode}} {
			h, p, err := d.run(context.Background(), webpTestInput(c.n), pixels)
			if !errors.Is(err, c.want) || h.Width != 0 || p != nil {
				t.Fatal("native failure hidden", pixels, c.n, err)
			}
		}
		h, p, err := d.run(context.Background(), webpTestInput(22), pixels)
		if err != nil || h.Width != 1 || h.Height != 1 || h.Mode != "RGB" || pixels && !bytes.Equal(p, []byte{42, 43, 44}) || !pixels && p != nil {
			t.Fatal("native recovery", err)
		}
		if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
			t.Fatal(err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		done := make(chan error, 1)
		go func() {
			h, p, err := d.run(ctx, webpTestInput(17), pixels)
			if h.Width != 0 || p != nil {
				done <- errors.New("cancelled WebP exposed output")
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
				t.Fatal("decoder did not start")
			}
			time.Sleep(5 * time.Millisecond)
		}
		queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
		_, err = d.Inspect(queued, webpTestInput(22))
		stop()
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("decoder queue", err)
		}
		cancel()
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("decoder was not reaped")
		}
		if len(d.process.slots) != 0 {
			t.Fatal("decoder slot retained")
		}
		if _, err := d.Inspect(context.Background(), webpTestInput(22)); err != nil {
			t.Fatal("reuse after cancellation", err)
		}
	}
}

func TestWebPFileStageBudgetsAndCancellation(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_WEBP_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name       string
		Data, JPEG []byte
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	var input, expected []byte
	for _, c := range cases {
		if c.Name == "lossless RGB/1/1/constant" {
			input, expected = c.Data, c.JPEG
		}
	}
	if len(input) == 0 || len(expected) == 0 {
		t.Fatal("required scalar WebP")
	}
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	for _, stage := range []string{"input", "inspect", "decode", "transform", "raster", "output", "exact"} {
		l := webpTestLimits()
		switch stage {
		case "input":
			l.Codec.MaxEncodedBytes = len(input) - 1
		case "inspect":
			l.MaxDecodeBytes = int64(len(input) + 7)
		case "decode":
			l.MaxDecodeBytes = int64(len(input) + 8)
		case "transform":
			l.MaxTransformBytes = 1
		case "raster":
			l.Codec.MaxRasterBytes = 1
		case "output":
			l.Codec.MaxEncodedBytes = 200
		case "exact":
			l.MaxDecodeBytes = int64(len(input) + 11)
		}
		p, err := NewWebPFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		if stage == "decode" {
			if _, err := p.decoder.Inspect(context.Background(), input); err != nil {
				t.Fatal("decode case did not pass header stage", err)
			}
		}
		got, err := p.Process(context.Background(), input)
		if stage == "exact" {
			if err != nil || got.KeepOriginal || !bytes.Equal(got.JPEG, expected) {
				t.Fatal("exact native budget", err)
			}
		} else if !errors.Is(err, ErrWorkingLimit) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("stage budget hidden", stage, err)
		}
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	for _, phase := range []string{"inspect", "encode"} {
		if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
			t.Fatal(err)
		}
		l := webpTestLimits()
		l.Codec.Concurrency = 1
		p, err := NewWebPFileProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		originalDecoder, originalEncoder := p.decoder, p.encoder
		data := input
		if phase == "inspect" {
			p.decoder, err = NewWebPDecoder(fault, WebPLimits{l.Codec.MaxEncodedBytes, l.MaxDecodeBytes, 1, l.Codec.Timeout})
			data = webpTestInput(17)
		} else {
			p.encoder, err = NewJPEGEncoder(fault, l.Codec)
		}
		if err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		done := make(chan error, 1)
		go func() {
			got, err := p.Process(ctx, data)
			if got.KeepOriginal || got.JPEG != nil {
				done <- errors.New("cancelled file exposed result")
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
				t.Fatal("native file stage did not start", phase)
			}
			time.Sleep(5 * time.Millisecond)
		}
		queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
		got, err := p.Process(queued, data)
		stop()
		if !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
			t.Fatal("whole file admission", err)
		}
		cancel()
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("file child was not reaped")
		}
		if len(p.slots) != 0 || len(p.decoder.process.slots) != 0 || len(p.encoder.slots) != 0 {
			t.Fatal("file slot retained")
		}
		p.decoder, p.encoder = originalDecoder, originalEncoder
		if got, err := p.Process(context.Background(), input); err != nil || got.KeepOriginal || !bytes.Equal(got.JPEG, expected) {
			t.Fatal("whole pipeline reuse", err)
		}
	}
	l := webpTestLimits()
	l.Codec.Timeout = 20 * time.Millisecond
	p, err := NewWebPFileProcessor(fault, l)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := p.Process(context.Background(), webpTestInput(17)); !errors.Is(err, context.DeadlineExceeded) || got.KeepOriginal || got.JPEG != nil {
		t.Fatal("whole file deadline", err)
	}
}
