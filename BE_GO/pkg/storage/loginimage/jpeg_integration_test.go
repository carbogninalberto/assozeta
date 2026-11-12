//go:build imageintegration

package loginimage

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestJPEGAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_LOGIN_JPEG_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_jpeg.py", err)
	}
	var vectors []struct {
		Name, Mode       string
		Width, Height    int
		Pixels, Expected []byte
	}
	if err := json.Unmarshal(data, &vectors); err != nil || len(vectors) != 180 {
		t.Fatal("invalid JPEG fixture", err)
	}
	limits := jpegTestLimits()
	limits.Concurrency = 3
	limits.Timeout = 10 * time.Second
	encoder, err := NewJPEGEncoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range vectors {
		t.Run(tc.Name, func(t *testing.T) {
			original := append([]byte(nil), tc.Pixels...)
			result, err := encoder.Encode(context.Background(), Raster{tc.Width, tc.Height, tc.Mode, tc.Pixels})
			if err != nil || !bytes.Equal(result, tc.Expected) {
				t.Fatalf("JPEG bytes differ: got %d, want %d: %v", len(result), len(tc.Expected), err)
			}
			if !bytes.Equal(tc.Pixels, original) {
				t.Fatal("source mutated")
			}
		})
	}
	tc := vectors[71] // RGB noise 129x97, spanning multiple destination chunks.
	if len(tc.Expected) <= 4096 {
		t.Fatal("output growth case no longer spans chunks")
	}
	for _, delta := range []int{-1, 0, 1} {
		bound := limits
		bound.MaxEncodedBytes = len(tc.Expected) + delta
		limited, err := NewJPEGEncoder(encoder.path, bound)
		if err != nil {
			t.Fatal(err)
		}
		result, err := limited.Encode(context.Background(), Raster{tc.Width, tc.Height, tc.Mode, tc.Pixels})
		if delta < 0 {
			if result != nil || !errors.Is(err, ErrWorkingLimit) {
				t.Fatal("output budget not enforced", err)
			}
		} else if err != nil || !bytes.Equal(result, tc.Expected) {
			t.Fatal("exact output budget rejected", err)
		}
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := encoder.Encode(context.Background(), Raster{tc.Width, tc.Height, tc.Mode, tc.Pixels})
			if err != nil || !bytes.Equal(result, tc.Expected) {
				t.Error("concurrent JPEG differs", err)
			}
		}()
	}
	wg.Wait()
	if len(encoder.slots) != 0 {
		t.Fatal("encoder slot leaked")
	}
}

func TestJPEGProcessFailureAndAdmission(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	limits := jpegTestLimits()
	limits.Concurrency = 1
	limits.Timeout = 5 * time.Second
	limits.MaxEncodedBytes = 4096
	encoder, err := NewJPEGEncoder(path, limits)
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_jpeg.py", err)
	}
	t.Setenv("ASSOZETA_TEST_SECRET", "must-not-reach-codec")
	image := func(width int) Raster { return Raster{width, 1, "L", make([]byte, width)} }
	for _, tc := range []struct {
		width int
		want  error
	}{{2, ErrWorkingLimit}, {3, ErrWorkingLimit}, {4, ErrJPEGEncode}, {5, ErrJPEGEncode}, {6, nil}} {
		result, err := encoder.Encode(context.Background(), image(tc.width))
		if !errors.Is(err, tc.want) || tc.want != nil && result != nil {
			t.Fatalf("width %d: %v", tc.width, err)
		}
		if len(encoder.slots) != 0 {
			t.Fatal("failed process retained a slot")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		result, err := encoder.Encode(ctx, image(1))
		if result != nil {
			done <- errors.New("cancelled process returned bytes")
			return
		}
		done <- err
	}()
	deadline := time.Now().Add(2 * time.Second)
	for {
		if data, err := os.ReadFile(path + ".started"); err == nil && len(data) > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("blocking codec did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer stop()
	if result, err := encoder.Encode(queued, image(1)); result != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("queued request was not cancelled", err)
	}
	data, err := os.ReadFile(path + ".started")
	if err != nil || string(data) != "1" {
		t.Fatal("queued request spawned another process")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("running process not cancelled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("child was not killed/reaped")
	}
	if len(encoder.slots) != 0 {
		t.Fatal("cancelled process retained a slot")
	}
	limited := limits
	limited.Timeout = 30 * time.Millisecond
	timed, err := NewJPEGEncoder(path, limited)
	if err != nil {
		t.Fatal(err)
	}
	if result, err := timed.Encode(context.Background(), image(1)); result != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("running codec timeout lost", err)
	}
	if len(timed.slots) != 0 {
		t.Fatal("timeout retained a slot")
	}
	// A previously valid executable can disappear before a later request.
	local := filepath.Join(t.TempDir(), "removed-codec")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(local, raw, 0700); err != nil {
		t.Fatal(err)
	}
	removed, err := NewJPEGEncoder(local, limits)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(local); err != nil {
		t.Fatal(err)
	}
	if result, err := removed.Encode(context.Background(), image(6)); result != nil || !errors.Is(err, ErrJPEGCodecUnavailable) {
		t.Fatal("removed codec not classified", err)
	}
}

func TestJPEGNativeProtocol(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	if !filepath.IsAbs(path) {
		t.Fatal("run TESTS/adapters/django/login_jpeg.py")
	}
	for _, tc := range []struct {
		args  []string
		input []byte
		code  int
	}{
		{nil, nil, 2},
		{[]string{"encode", "RGBA", "1", "1", "1000"}, make([]byte, 4), 2},
		{[]string{"encode", "L", "0", "1", "1000"}, nil, 2},
		{[]string{"encode", "L", "65501", "1", "1000"}, nil, 2},
		{[]string{"encode", "L", "-1", "1", "1000"}, nil, 2},
		{[]string{"encode", "L", "1", "1", "0"}, nil, 2},
		{[]string{"encode", "L", strings.Repeat("9", 100), "1", "1000"}, nil, 2},
		{[]string{"encode", "RGB", "1", "1", "1000"}, make([]byte, 2), 2},
		{[]string{"encode", "RGB", "1", "1", "1000"}, make([]byte, 4), 2},
		{[]string{"encode", "RGB", "1", "1", "1"}, make([]byte, 3), 4},
	} {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		cmd := exec.CommandContext(ctx, path, tc.args...)
		cmd.Stdin = bytes.NewReader(tc.input)
		cmd.Env = []string{"LC_ALL=C"}
		output, err := cmd.Output()
		cancel()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != tc.code || len(output) != 0 {
			t.Fatalf("invalid protocol returned bytes or unexpected status: %v", err)
		}
	}
}
