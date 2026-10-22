package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotPIXAR        = errors.New("login image requires another file decoder")
	ErrPIXARFileLimits = errors.New("login PIXAR file limits invalid")
)

type PIXARFileLimits = BMPFileLimits
type PIXARFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// PIXARFileProcessor applies compress_base64's lazy admission and transform policy
// to PIXAR raw RGB. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type PIXARFileProcessor struct {
	encoder *JPEGEncoder
	limits  PIXARFileLimits
	slots   chan struct{}
}

func NewPIXARFileProcessor(path string, limits PIXARFileLimits) (*PIXARFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrPIXARFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &PIXARFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *PIXARFileProcessor) Process(ctx context.Context, input []byte) (PIXARFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *PIXARFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (PIXARFileResult, error) {
	empty := PIXARFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !pixarSignature(input) {
		return empty, ErrNotPIXAR
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (PIXARFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return PIXARFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadPIXARHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrPIXARHeader) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	plan, err := decideSize(h.Width, h.Height, len(input), maxSize)
	if errors.Is(err, ErrDimensions) || plan.KeepOriginal {
		return original()
	}
	if err != nil {
		return empty, err
	}
	decoded, err := DecodePIXAR(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrPIXARDecode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	raster, err := Transform(ctx, decoded, plan.Width, plan.Height, p.limits.MaxTransformBytes)
	if errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	encoded, err := p.encoder.Encode(ctx, raster)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return PIXARFileResult{JPEG: encoded}, nil
}
