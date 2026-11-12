//go:build imageintegration

package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestPNGStreamAgainstPillow(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_PNG_STREAM_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name           string
		Data, Expected []byte
		Spec           struct {
			PNGPixelSpec
			DataOffset int `json:"data_offset"`
			DataLength int `json:"data_length"`
			Sequence   int64
		}
		Mode                string
		Invalid             bool
		Position, Remaining int
		Sequence            int64
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) != 852 {
		t.Fatal("invalid PNG stream fixture", err, len(cases))
	}
	limits := PNGStreamLimits{MaxInputBytes: 8 << 20, MaxWorkingBytes: 16 << 20, Concurrency: 4, Timeout: 10 * time.Second}
	decoder, err := NewPNGStreamDecoder(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), limits)
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		tc := cases[i]
		spec := PNGStreamSpec{tc.Spec.PNGPixelSpec, tc.Spec.DataOffset, tc.Spec.DataLength, tc.Spec.Sequence}
		original := bytes.Clone(tc.Data)
		got, err := decoder.Decode(context.Background(), tc.Data, spec)
		if tc.Invalid {
			if !errors.Is(err, ErrPNGDecode) || got.Pixels != nil {
				t.Errorf("%s: invalid stream accepted: %v", tc.Name, err)
			}
			return
		}
		if err != nil || got.Mode != tc.Mode || got.Width != spec.Width || got.Height != spec.Height || !bytes.Equal(got.Pixels, tc.Expected) || got.Position != tc.Position || got.Remaining != tc.Remaining || got.Sequence != tc.Sequence {
			t.Errorf("%s: stream differs: mode=%s position=%d remaining=%d sequence=%d: %v", tc.Name, got.Mode, got.Position, got.Remaining, got.Sequence, err)
		}
		if !bytes.Equal(original, tc.Data) {
			t.Error("input mutated")
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(744) }()
	}
	wg.Wait()
}

func TestPNGStreamNativeProtocol(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	data := pngCompressed(t, []byte{0, 42})
	base := []string{"png-inflate", strconv.Itoa(len(data)), "1", "1", "8", "0", "0", strconv.Itoa(len(data)), "-1", "2"}
	run := func(args []string, input []byte, code int) []byte {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, path, args...)
		cmd.Env = []string{"LC_ALL=C"}
		cmd.Stdin = bytes.NewReader(input)
		out, err := cmd.Output()
		if code == 0 {
			if err != nil {
				t.Fatal(err)
			}
			return out
		}
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != code || len(out) != 0 {
			t.Fatalf("protocol: got %v, bytes=%d; want exit %d", err, len(out), code)
		}
		return nil
	}
	frame := run(base, data, 0)
	if len(frame) != 42 || string(frame[:4]) != "AZPN" || binary.BigEndian.Uint64(frame[8:16]) != uint64(len(data)) || binary.BigEndian.Uint64(frame[32:40]) != 2 || !bytes.Equal(frame[40:], []byte{0, 42}) {
		t.Fatal("wrong native frame")
	}
	for _, tc := range []struct {
		index int
		value string
		code  int
	}{
		{1, "0", 2}, {1, "-1", 2}, {1, strings.Repeat("9", 100), 2}, {2, "0", 2}, {3, "0", 2}, {4, "3", 2}, {5, "2", 2},
		{6, strconv.Itoa(len(data) + 1), 2}, {7, "4294967296", 2}, {8, "-2", 2}, {8, "4294967296", 2}, {9, "0", 2}, {9, "1", 4},
		{2, "9223372036854775807", 4},
	} {
		args := append([]string(nil), base...)
		args[tc.index] = tc.value
		run(args, data, tc.code)
	}
	run(base[:3], data, 2)
	run(base, data[:len(data)-1], 2)
	run(base, append(bytes.Clone(data), 0), 2)
	// An active chunk may declare more bytes than are actually present.
	args := append([]string(nil), base...)
	args[7] = "70000"
	frame = run(args, data, 0)
	if binary.BigEndian.Uint64(frame[16:24]) != 4464 {
		t.Fatal("native lost requested-byte remainder")
	}
}

func TestPNGStreamBudgetsAndFailures(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC")
	data := pngCompressed(t, []byte{0, 42})
	spec := PNGStreamSpec{PNGPixelSpec: PNGPixelSpec{Width: 1, Height: 1, Depth: 8}, DataLength: len(data), Sequence: -1}
	exact := int64(len(data) + 40 + 2 + 1 + 2 + 1)
	for _, delta := range []int64{-1, 0, 1} {
		d, err := NewPNGStreamDecoder(path, PNGStreamLimits{len(data), exact + delta, 1, time.Second})
		if err != nil {
			t.Fatal(err)
		}
		got, err := d.Decode(context.Background(), data, spec)
		if delta < 0 {
			if !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
				t.Fatal("budget ignored", err)
			}
		} else if err != nil || !bytes.Equal(got.Pixels, []byte{42}) {
			t.Fatal("exact budget rejected", err)
		}
	}
	d, err := NewPNGStreamDecoder(path, PNGStreamLimits{len(data) - 1, 1024, 1, time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if got, err := d.Decode(context.Background(), data, spec); !errors.Is(err, ErrWorkingLimit) || got.Pixels != nil {
		t.Fatal("input budget ignored", err)
	}
	d, err = NewPNGStreamDecoder(path, PNGStreamLimits{1024, 1024, 1, time.Second})
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []PNGStreamSpec{
		{PNGPixelSpec: spec.PNGPixelSpec, DataOffset: -1, Sequence: -1},
		{PNGPixelSpec: spec.PNGPixelSpec, DataLength: -1, Sequence: -1},
		{PNGPixelSpec: spec.PNGPixelSpec, DataOffset: len(data) + 1, Sequence: -1},
		{PNGPixelSpec: spec.PNGPixelSpec, DataLength: len(data), Sequence: -2},
	} {
		if got, err := d.Decode(context.Background(), data, bad); !errors.Is(err, ErrPNGDecode) || got.Pixels != nil {
			t.Fatal("invalid stream spec accepted", err)
		}
	}
	fault := os.Getenv("ASSOZETA_LOGIN_JPEG_FAULT_CODEC")
	d, err = NewPNGStreamDecoder(fault, PNGStreamLimits{1024, 1024, 1, 5 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("ASSOZETA_TEST_SECRET", "must-not-reach-codec")
	input := []byte{1, 2, 3}
	call := func(ctx context.Context, width int) (PNGStreamImage, error) {
		return d.Decode(ctx, input, PNGStreamSpec{PNGPixelSpec: PNGPixelSpec{Width: width, Height: 1, Depth: 8}, DataLength: 3, Sequence: -1})
	}
	for _, tc := range []struct {
		width int
		want  error
	}{{2, ErrWorkingLimit}, {3, ErrWorkingLimit}, {4, ErrPNGDecode}, {5, ErrPNGDecode}, {6, nil}} {
		got, err := call(context.Background(), tc.width)
		if !errors.Is(err, tc.want) || tc.want != nil && got.Pixels != nil {
			t.Fatal("fault mapping", tc.width, err)
		}
		if tc.want == nil && !bytes.Equal(got.Pixels, bytes.Repeat([]byte{42}, 6)) {
			t.Fatal("wrong recovered pixels")
		}
		if len(d.process.slots) != 0 {
			t.Fatal("process slot leaked")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		got, err := call(ctx, 1)
		if got.Pixels != nil {
			done <- errors.New("cancelled pixels exposed")
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
			t.Fatal("child did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	queued, stop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	got, err := call(queued, 1)
	stop()
	if !errors.Is(err, context.DeadlineExceeded) || got.Pixels != nil {
		t.Fatal("queued cancellation lost", err)
	}
	if b, err := os.ReadFile(fault + ".started"); err != nil || string(b) != "1" {
		t.Fatal("queued request spawned child")
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
	if got, err := call(context.Background(), 6); err != nil || len(got.Pixels) != 6 || len(d.process.slots) != 0 {
		t.Fatal("admission not reusable", err)
	}
	timed, err := NewPNGStreamDecoder(fault, PNGStreamLimits{1024, 1024, 1, 30 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if got, err := timed.Decode(context.Background(), input, PNGStreamSpec{PNGPixelSpec: PNGPixelSpec{Width: 1, Height: 1, Depth: 8}, DataLength: 3, Sequence: -1}); !errors.Is(err, context.DeadlineExceeded) || got.Pixels != nil || len(timed.process.slots) != 0 {
		t.Fatal("timeout failed", err)
	}
	local := filepath.Join(t.TempDir(), "removed-codec")
	binary, err := os.ReadFile(fault)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(local, binary, 0700); err != nil {
		t.Fatal(err)
	}
	removed, err := NewPNGStreamDecoder(local, PNGStreamLimits{1024, 1024, 1, time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(local); err != nil {
		t.Fatal(err)
	}
	if got, err := removed.Decode(context.Background(), data, spec); !errors.Is(err, ErrPNGCodecUnavailable) || got.Pixels != nil {
		t.Fatal("removed executable", err)
	}
}
