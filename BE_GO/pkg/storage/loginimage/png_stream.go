package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"io"
	"math"
	"strconv"
	"time"
)

var ErrPNGStreamLimits = errors.New("invalid login PNG stream limits")
var ErrPNGCodecUnavailable = errors.New("login PNG codec unavailable")

// PNGStreamSpec describes an admitted tile and the first compressed payload.
// Sequence is the last APNG sequence, or -1 when no fcTL/fdAT was admitted.
// Width/Height describe the tile, not the full APNG canvas.
type PNGStreamSpec struct {
	PNGPixelSpec
	DataOffset, DataLength int
	Sequence               int64
}

// PNGStreamImage includes the file-reader state after the final load_read call.
// Remaining counts declared bytes, even if the active chunk was truncated.
// Post-pixel parsing must start here, skipping Remaining bytes and the CRC.
type PNGStreamImage struct {
	Raster
	Position, Remaining int
	Sequence            int64
}

type PNGStreamLimits struct {
	MaxInputBytes   int
	MaxWorkingBytes int64
	Concurrency     int
	Timeout         time.Duration
}

// PNGStreamDecoder preserves per-chunk zlib error timing in an isolated native
// process, then applies Go filters/sample unpacking. It does not admit metadata,
// compose an APNG canvas or validate the post-pixel tail. Share one instance for
// bounded child admission. Budgets cover logical payloads, not process RSS.
type PNGStreamDecoder struct {
	process *jpegProcess
	limits  PNGStreamLimits
}

func NewPNGStreamDecoder(path string, limits PNGStreamLimits) (*PNGStreamDecoder, error) {
	if limits.MaxWorkingBytes <= 0 || limits.MaxWorkingBytes > int64(^uint(0)>>1)-40 {
		return nil, ErrPNGStreamLimits
	}
	p, err := newJPEGProcess(path, JPEGLimits{MaxRasterBytes: limits.MaxWorkingBytes, MaxEncodedBytes: limits.MaxInputBytes, Concurrency: limits.Concurrency, Timeout: limits.Timeout})
	if errors.Is(err, ErrJPEGLimits) {
		return nil, ErrPNGStreamLimits
	}
	if errors.Is(err, ErrJPEGCodecUnavailable) {
		return nil, ErrPNGCodecUnavailable
	}
	if err != nil {
		return nil, err
	}
	return &PNGStreamDecoder{p, limits}, nil
}

func pngStreamLayout(spec PNGPixelSpec) (mode string, sourceChannels, outputChannels int, pixels, rowBytes, rawBytes int64, err error) {
	mode, sourceChannels, outputChannels = pngPixelMode(spec.Depth, spec.Color)
	if mode == "" {
		err = ErrPNGDecode
		return
	}
	pixels, err = rasterBytes(spec.Width, spec.Height, outputChannels)
	if err != nil {
		return
	}
	bits := int64(sourceChannels * spec.Depth)
	if int64(spec.Width) > (math.MaxInt64-7)/bits {
		err = ErrWorkingLimit
		return
	}
	rowBytes = (int64(spec.Width)*bits + 7) / 8
	passes := [][4]int{{0, 0, 1, 1}}
	if spec.Interlace {
		passes = [][4]int{{0, 0, 8, 8}, {4, 0, 8, 8}, {0, 4, 4, 8}, {2, 0, 4, 4}, {0, 2, 2, 4}, {1, 0, 2, 2}, {0, 1, 1, 2}}
	}
	for _, p := range passes {
		if spec.Width <= p[0] || spec.Height <= p[1] {
			continue
		}
		w := int64(1 + (spec.Width-p[0]-1)/p[2])
		h := int64(1 + (spec.Height-p[1]-1)/p[3])
		row := (w*bits+7)/8 + 1
		if h > (math.MaxInt64-rawBytes)/row {
			err = ErrWorkingLimit
			return
		}
		rawBytes += row * h
	}
	return
}

func (d *PNGStreamDecoder) Decode(ctx context.Context, data []byte, spec PNGStreamSpec) (PNGStreamImage, error) {
	empty := PNGStreamImage{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	ctx, cancel := context.WithTimeout(ctx, d.limits.Timeout)
	defer cancel()
	if len(data) > d.limits.MaxInputBytes {
		return empty, ErrWorkingLimit
	}
	if spec.DataOffset < 0 || spec.DataOffset > len(data) || spec.DataLength < 0 || uint64(spec.DataLength) > math.MaxUint32 || spec.Sequence < -1 || spec.Sequence > math.MaxUint32 {
		return empty, ErrPNGDecode
	}
	_, sourceChannels, _, pixels, rows, raw, err := pngStreamLayout(spec.PNGPixelSpec)
	if err != nil {
		return empty, err
	}
	used := int64(len(data))
	for _, n := range []int64{40, raw, pixels, rows + 1, rows} {
		if n > d.limits.MaxWorkingBytes-used {
			return empty, ErrWorkingLimit
		}
		used += n
	}
	interlace := "0"
	if spec.Interlace {
		interlace = "1"
	}
	args := []string{"png-inflate", strconv.Itoa(len(data)), strconv.Itoa(spec.Width), strconv.Itoa(spec.Height), strconv.Itoa(sourceChannels * spec.Depth), interlace, strconv.Itoa(spec.DataOffset), strconv.Itoa(spec.DataLength), strconv.FormatInt(spec.Sequence, 10), strconv.FormatInt(raw, 10)}
	frame, err := d.process.run(ctx, args, bytes.NewReader(data), int(raw)+40, ErrPNGDecode)
	if errors.Is(err, ErrJPEGCodecUnavailable) {
		err = ErrPNGCodecUnavailable
	}
	if err != nil {
		return empty, err
	}
	return decodePNGStreamFrame(ctx, spec, frame, len(data), d.limits.MaxWorkingBytes-int64(len(data)))
}

func decodePNGStreamFrame(ctx context.Context, spec PNGStreamSpec, frame []byte, inputLength int, budget int64) (PNGStreamImage, error) {
	empty := PNGStreamImage{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if budget <= 0 {
		return empty, ErrWorkingLimit
	}
	if len(frame) < 40 || string(frame[:4]) != "AZPN" || binary.BigEndian.Uint32(frame[4:8]) != 0 {
		return empty, ErrPNGDecode
	}
	position := binary.BigEndian.Uint64(frame[8:16])
	remaining := binary.BigEndian.Uint64(frame[16:24])
	sequence := binary.BigEndian.Uint64(frame[24:32])
	length := binary.BigEndian.Uint64(frame[32:40])
	if inputLength < 0 || spec.DataOffset < 0 || position < uint64(spec.DataOffset) || position > uint64(inputLength) || remaining > math.MaxUint32 || remaining > uint64(^uint(0)>>1) || length == 0 || length != uint64(len(frame)-40) {
		return empty, ErrPNGDecode
	}
	if sequence != math.MaxUint64 && (sequence > math.MaxUint32 || int64(sequence) < spec.Sequence) || sequence == math.MaxUint64 && spec.Sequence != -1 {
		return empty, ErrPNGDecode
	}
	mode, sourceChannels, outputChannels, pixels, rows, raw, err := pngStreamLayout(spec.PNGPixelSpec)
	if err != nil {
		return empty, err
	}
	if length > uint64(raw) {
		return empty, ErrPNGDecode
	}
	used := int64(len(frame))
	for _, n := range []int64{pixels, rows + 1, rows} {
		if n > budget-used {
			return empty, ErrWorkingLimit
		}
		used += n
	}
	reader := bytes.NewReader(frame[40:])
	raster, err := decodePNGRows(ctx, spec.PNGPixelSpec, mode, sourceChannels, outputChannels, pixels, rows, func(row []byte) (bool, error) {
		_, err := io.ReadFull(reader, row)
		return reader.Len() == 0, err
	})
	if err != nil {
		return empty, err
	}
	if reader.Len() != 0 {
		return empty, ErrPNGDecode
	}
	return PNGStreamImage{raster, int(position), int(remaining), int64(sequence)}, nil
}
