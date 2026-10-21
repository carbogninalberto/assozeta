package loginimage

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"
)

var ErrJPEGLimits = errors.New("invalid login JPEG limits")
var ErrJPEGCodecUnavailable = errors.New("login JPEG codec unavailable")
var ErrJPEGEncode = errors.New("login JPEG encoding failed")

// JPEGLimits bounds input/output payloads and simultaneous encoder processes.
// Timeout includes queue time. These are not a total process RSS guarantee.
type JPEGLimits struct {
	MaxRasterBytes  int64
	MaxEncodedBytes int
	Concurrency     int
	Timeout         time.Duration
}

// JPEGEncoder runs the bundled libjpeg helper without a shell. Construct one
// shared instance to enforce process admission across requests. It accepts
// normalized rasters after Transform; it does not open image files.
type JPEGEncoder struct {
	path   string
	limits JPEGLimits
	slots  chan struct{}
}

func NewJPEGEncoder(path string, limits JPEGLimits) (*JPEGEncoder, error) {
	if limits.MaxRasterBytes <= 0 || limits.MaxEncodedBytes <= 0 || limits.Concurrency <= 0 || limits.Timeout <= 0 {
		return nil, ErrJPEGLimits
	}
	info, err := os.Stat(path)
	if !filepath.IsAbs(path) || err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return nil, ErrJPEGCodecUnavailable
	}
	return &JPEGEncoder{path: path, limits: limits, slots: make(chan struct{}, limits.Concurrency)}, nil
}

func (e *JPEGEncoder) Encode(ctx context.Context, input Raster) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	c := channels(input.Mode)
	if c == 0 || input.Mode == "RGBA" {
		return nil, ErrUnsupportedMode
	}
	// libjpeg's JPEG_MAX_DIMENSION, also enforced by the native helper.
	if input.Width > 65500 || input.Height > 65500 {
		return nil, ErrDimensions
	}
	size, err := rasterBytes(input.Width, input.Height, c)
	if err != nil {
		return nil, err
	}
	if size != int64(len(input.Pixels)) {
		return nil, ErrRaster
	}
	if size > e.limits.MaxRasterBytes {
		return nil, ErrWorkingLimit
	}
	runCtx, cancel := context.WithTimeout(ctx, e.limits.Timeout)
	defer cancel()
	select {
	case e.slots <- struct{}{}:
		defer func() { <-e.slots }()
	case <-runCtx.Done():
		return nil, runCtx.Err()
	}
	if err := runCtx.Err(); err != nil {
		return nil, err
	}
	// Separate cancellation lets an overflowing stdout kill the child without
	// confusing the externally visible request/timeout error with our own kill.
	childCtx, kill := context.WithCancel(runCtx)
	defer kill()
	output := jpegOutput{limit: e.limits.MaxEncodedBytes, kill: kill}
	cmd := exec.CommandContext(childCtx, e.path, "encode", input.Mode, strconv.Itoa(input.Width), strconv.Itoa(input.Height), strconv.Itoa(e.limits.MaxEncodedBytes))
	cmd.Env = []string{"LC_ALL=C"}
	cmd.Stdin = bytes.NewReader(input.Pixels)
	cmd.Stdout = &output
	cmd.Stderr = io.Discard
	cmd.WaitDelay = time.Second
	err = cmd.Run()
	if requestErr := runCtx.Err(); requestErr != nil {
		return nil, requestErr
	}
	if output.overflow {
		return nil, ErrWorkingLimit
	}
	if err != nil {
		var status *exec.ExitError
		if errors.As(err, &status) {
			if status.ExitCode() == 4 {
				return nil, ErrWorkingLimit
			}
			return nil, ErrJPEGEncode
		}
		var pathError *os.PathError
		if errors.As(err, &pathError) {
			return nil, ErrJPEGCodecUnavailable
		}
		return nil, ErrJPEGEncode
	}
	result := output.buffer.Bytes()
	if len(result) < 4 || result[0] != 0xff || result[1] != 0xd8 || result[len(result)-2] != 0xff || result[len(result)-1] != 0xd9 {
		return nil, ErrJPEGEncode
	}
	return result, nil
}

type jpegOutput struct {
	buffer   bytes.Buffer
	limit    int
	overflow bool
	kill     context.CancelFunc
}

func (w *jpegOutput) Write(p []byte) (int, error) {
	if len(p) > w.limit-w.buffer.Len() {
		w.overflow = true
		w.kill()
		return 0, ErrWorkingLimit
	}
	return w.buffer.Write(p)
}
