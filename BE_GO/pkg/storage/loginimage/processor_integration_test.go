//go:build imageintegration

package loginimage

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestImageProcessorAgainstDjango(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_IMAGE_DISPATCH_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, URI, Expected string
		Gap                 bool
	}
	if err = json.Unmarshal(raw, &cases); err != nil || len(cases) != 73 {
		t.Fatal("required dispatch fixture", len(cases), err)
	}
	p, err := NewProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), imageProcessorTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		c := cases[i]
		got, err := p.CompressLoginImage(context.Background(), c.URI)
		if c.Gap {
			if !errors.Is(err, ErrImageDecoderUnavailable) || got != "" {
				t.Errorf("%s: incomplete decoder silently accepted: %v", c.Name, err)
			}
			return
		}
		if err != nil || got != c.Expected {
			t.Errorf("%s: response differs, err=%v", c.Name, err)
			_, payload, _ := strings.Cut(c.URI, ",")
			raw, _ := base64.StdEncoding.DecodeString(payload)
			for _, f := range p.formats {
				if f.accepts(raw) {
					t.Log(c.Name, f.name)
				}
			}
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
		t.Fatal("whole-image admission leaked")
	}
}

func TestImageProcessorSharedAdmissionAcrossFormats(t *testing.T) {
	l := imageProcessorTestLimits()
	l.Codec.Timeout = 10 * time.Second
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	if err := os.Remove(fault + ".started"); err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	p, err := NewProcessor(fault, l)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 2)
	for _, input := range [][]byte{[]byte("P5\n1 1\n255\nI"), gifTestFile(1, 1, []byte{73})} {
		go func() {
			got, err := p.CompressLoginImage(ctx, imageURI(input))
			if got != "" {
				done <- errors.New("cancelled decoder exposed output")
				return
			}
			done <- err
		}()
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		data, _ := os.ReadFile(fault + ".started")
		if string(data) == "11" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("mixed decoders did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	got, err := p.CompressLoginImage(queued, imageURI(tgaTestFile(1, 1, 3, 8, 32, []byte{73})))
	stop()
	if got != "" || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("global queue deadline", err)
	}
	if data, _ := os.ReadFile(fault + ".started"); string(data) != "11" {
		t.Fatal("third format bypassed global admission")
	}
	cancel()
	for i := 0; i < 2; i++ {
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("mixed child was not reaped")
		}
	}
	if len(p.slots) != 0 {
		t.Fatal("global admission retained after cancel")
	}
	value := imageURI(gifTestFile(200, 100, nil))
	if got, err := p.CompressLoginImage(context.Background(), value); err != nil || got != value {
		t.Fatal("processor not reusable", err)
	}
}
func TestImageProcessorOutputBudgetAndDeadline(t *testing.T) {
	l := imageProcessorTestLimits()
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	p, err := NewProcessor(path, l)
	if err != nil {
		t.Fatal(err)
	}
	value := imageURI([]byte("P5\n1 1\n255\nI"))
	expected, err := p.CompressLoginImage(context.Background(), value)
	if err != nil || !strings.HasPrefix(expected, jpegDataPrefix) {
		t.Fatal("baseline conversion", err)
	}
	for _, budget := range []int{len(expected), len(expected) - 1} {
		l.MaxURIBytes = budget
		p, err = NewProcessor(path, l)
		if err != nil {
			t.Fatal(err)
		}
		got, err := p.CompressLoginImage(context.Background(), value)
		if budget == len(expected) {
			if err != nil || got != expected {
				t.Fatal("exact output budget", err)
			}
		} else if got != "" || !errors.Is(err, ErrWorkingLimit) {
			t.Fatal("oversized output leaked", err)
		}
	}
	l = imageProcessorTestLimits()
	l.Codec.Concurrency = 1
	l.Codec.Timeout = 20 * time.Millisecond
	p, err = NewProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC"), l)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := p.CompressLoginImage(context.Background(), value); got != "" || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("full pipeline deadline", err)
	}
	if len(p.slots) != 0 {
		t.Fatal("deadline retained admission")
	}
}
