package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotMSP        = errors.New("login image requires another file decoder")
	ErrMSPFileLimits = errors.New("login MSP file limits invalid")
)

type MSPFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type MSPFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// MSPFileProcessor applies compress_base64's lazy admission and transform policy
// to MSP images. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type MSPFileProcessor struct {
	encoder *JPEGEncoder
	limits  MSPFileLimits
	slots   chan struct{}
}

func NewMSPFileProcessor(path string, limits MSPFileLimits) (*MSPFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrMSPFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &MSPFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *MSPFileProcessor) Process(ctx context.Context, input []byte) (MSPFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *MSPFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (MSPFileResult, error) {
	empty := MSPFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !mspSignature(input) {
		return empty, ErrNotMSP
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (MSPFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return MSPFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadMSPHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrMSPHeader) {
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
	decoded, err := DecodeMSP(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrMSPDecode) || errors.Is(err, ErrDimensions) {
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
	return MSPFileResult{JPEG: encoded}, nil
}
