//go:build imageintegration

package loginimage

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestJPEGDecodeAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_jpeg_decode.py", err)
	}
	var vectors []struct {
		Name, Mode     string
		Width, Height  int
		Data, Expected []byte
		Comment        []byte
		ExpectedJPEG   []byte `json:"expected_jpeg"`
		Invalid        bool
		EncodeInvalid  bool `json:"encode_invalid"`
	}
	if err := json.Unmarshal(data, &vectors); err != nil || len(vectors) < 1800 {
		t.Fatal("invalid decoder fixture", err)
	}
	limits := jpegTestLimits()
	limits.Timeout = 10 * time.Second
	limits.Concurrency = 3
	decoder, err := NewJPEGDecoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	encoder, err := NewJPEGEncoder(decoder.path, limits)
	if err != nil {
		t.Fatal(err)
	}
	valid, bad := 0, 0
	for _, tc := range vectors {
		before := append([]byte(nil), tc.Data...)
		got, err := decoder.Decode(context.Background(), tc.Data)
		if tc.Invalid {
			bad++
			if got.Pixels != nil || !errors.Is(err, ErrJPEGDecode) {
				t.Errorf("%s: invalid JPEG accepted: %v", tc.Name, err)
			}
			continue
		}
		valid++
		if err != nil || got.Width != tc.Width || got.Height != tc.Height || got.Mode != tc.Mode || (!bytes.Equal(got.Pixels, tc.Expected) || !bytes.Equal(got.Comment, tc.Comment) || (got.Comment == nil) != (tc.Comment == nil)) {
			t.Errorf("%s: decoded pixels differ (%d/%d): %v", tc.Name, len(got.Pixels), len(tc.Expected), err)
		}
		if err == nil {
			plan, err := Decide(got.Width, got.Height, len(tc.Data))
			if err != nil || plan.KeepOriginal {
				t.Fatal("unexpected JPEG fixture size policy", err)
			}
			prepared, err := Transform(context.Background(), got.Raster, plan.Width, plan.Height, 64<<20)
			if err != nil {
				t.Fatal(err)
			}
			encoded, err := encoder.EncodeWithComment(context.Background(), prepared, got.Comment)
			if tc.EncodeInvalid {
				if !errors.Is(err, ErrJPEGEncode) || encoded != nil {
					t.Errorf("%s: failed Pillow save accepted", tc.Name)
				}
			} else if err != nil || !bytes.Equal(encoded, tc.ExpectedJPEG) {
				t.Errorf("%s: JPEG decode/transform/encode differs: %v", tc.Name, err)
			}
		}
		if !bytes.Equal(before, tc.Data) {
			t.Fatal("encoded input mutated")
		}
	}
	if valid != 314 || bad != 1556 {
		t.Fatalf("oracle coverage changed: %d valid, %d invalid", valid, bad)
	}
	tc := vectors[71]
	for _, delta := range []int64{-1, 0, 1} {
		bound := limits
		bound.MaxRasterBytes = int64(len(tc.Expected)) + delta
		limited, err := NewJPEGDecoder(decoder.path, bound)
		if err != nil {
			t.Fatal(err)
		}
		got, err := limited.Decode(context.Background(), tc.Data)
		if delta < 0 {
			if got.Pixels != nil || !errors.Is(err, ErrWorkingLimit) {
				t.Fatal("native raster budget not enforced", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, tc.Expected) {
			t.Fatal("exact raster budget rejected", err)
		}
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got, err := decoder.Decode(context.Background(), tc.Data)
			if err != nil || !bytes.Equal(got.Pixels, tc.Expected) {
				t.Error("concurrent decode differs", err)
			}
		}()
	}
	wg.Wait()
	if len(decoder.slots) != 0 {
		t.Fatal("decoder admission leaked")
	}
}

func TestJPEGDecodeProcessFailures(t *testing.T) {
	limits := jpegTestLimits()
	limits.Concurrency = 1
	limits.Timeout = time.Second
	limits.MaxRasterBytes = 64
	decoder, err := NewJPEGDecoder(os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("ASSOZETA_TEST_SECRET", "not inherited")
	input := func(code int) []byte { return append([]byte{255, 216, 255}, make([]byte, code)...) }
	for _, tc := range []struct {
		code int
		want error
	}{{2, ErrWorkingLimit}, {3, ErrWorkingLimit}, {4, ErrJPEGDecode}, {5, ErrJPEGDecode}, {6, nil}} {
		got, err := decoder.Decode(context.Background(), input(tc.code))
		if !errors.Is(err, tc.want) || tc.want != nil && got.Pixels != nil {
			t.Fatalf("fault %d: %v", tc.code, err)
		}
		if tc.want == nil && (got.Width != 1 || got.Height != 1 || got.Mode != "L" || !bytes.Equal(got.Pixels, []byte{42})) {
			t.Fatal("valid frame lost")
		}
		if len(decoder.slots) != 0 {
			t.Fatal("decoder slot leaked")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		got, err := decoder.Decode(ctx, input(1))
		if got.Pixels != nil {
			done <- errors.New("cancelled decoder returned pixels")
			return
		}
		done <- err
	}()
	deadline := time.Now().Add(2 * time.Second)
	for {
		if data, err := os.ReadFile(decoder.path + ".started"); err == nil && len(data) > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("decoder not started")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer stop()
	if got, err := decoder.Decode(queued, input(1)); got.Pixels != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("queued decode not cancelled", err)
	}
	data, err := os.ReadFile(decoder.path + ".started")
	if err != nil || string(data) != "1" {
		t.Fatal("queued decoder spawned")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("decode cancellation lost", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("decoder not killed/reaped")
	}
	if len(decoder.slots) != 0 {
		t.Fatal("cancelled decoder retained slot")
	}
	limits.Timeout = 30 * time.Millisecond
	timed, err := NewJPEGDecoder(decoder.path, limits)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := timed.Decode(context.Background(), input(1)); got.Pixels != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("decoder timeout lost", err)
	}
	if len(timed.slots) != 0 {
		t.Fatal("timeout retained slot")
	}
}
func TestJPEGDecodeNativeProtocol(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/login_jpeg_decode.py")
	}
	for _, tc := range []struct {
		args  []string
		input []byte
	}{
		{[]string{"decode"}, nil},
		{[]string{"decode", "0", "1"}, nil},
		{[]string{"decode", "1", "0"}, nil},
		{[]string{"decode", strings.Repeat("9", 100), "1"}, nil},
		{[]string{"decode", "3", "1"}, []byte{255, 216}},
		{[]string{"decode", "3", "1"}, []byte{255, 216, 255, 0}},
		{[]string{"decode", "3", "1"}, []byte{255, 216, 255}},
		{[]string{"decode", "3", "1"}, []byte{1, 2, 3}},
	} {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		cmd := exec.CommandContext(ctx, path, tc.args...)
		cmd.Stdin = bytes.NewReader(tc.input)
		cmd.Env = []string{"LC_ALL=C"}
		output, err := cmd.Output()
		cancel()
		var status *exec.ExitError
		if !errors.As(err, &status) || status.ExitCode() != 2 || len(output) != 0 {
			t.Fatal("native decode protocol leaked output or wrong status", err)
		}
	}
	data, err := os.ReadFile(os.Getenv("ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Data, Expected []byte
		Comment        []byte
	}
	if err := json.Unmarshal(data, &vectors); err != nil {
		t.Fatal(err)
	}
	tc := vectors[71]
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, "decode", strconv.Itoa(len(tc.Data)), strconv.Itoa(len(tc.Expected)-1))
	cmd.Stdin = bytes.NewReader(tc.Data)
	output, err := cmd.Output()
	var status *exec.ExitError
	if !errors.As(err, &status) || status.ExitCode() != 4 || len(output) != 0 {
		t.Fatal("native decoder budget leaked a partial frame", err)
	}
}
