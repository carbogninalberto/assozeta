package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotPCX        = errors.New("login image requires another file decoder")
	ErrPCXFileLimits = errors.New("login PCX file limits invalid")
)

type PCXFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type PCXFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// PCXFileProcessor applies compress_base64's lazy admission and transform policy
// to standalone PCX and first-frame DCX. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type PCXFileProcessor struct {
	encoder *JPEGEncoder
	limits  PCXFileLimits
	slots   chan struct{}
}

func NewPCXFileProcessor(path string, limits PCXFileLimits) (*PCXFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrPCXFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &PCXFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *PCXFileProcessor) Process(ctx context.Context, input []byte) (PCXFileResult, error) {
	empty := PCXFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !pcxSignature(input) {
		return empty, ErrNotPCX
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (PCXFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return PCXFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadPCXHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrPCXHeader) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	plan, err := Decide(h.Width, h.Height, len(input))
	if errors.Is(err, ErrDimensions) || plan.KeepOriginal {
		return original()
	}
	if err != nil {
		return empty, err
	}
	decoded, err := DecodePCX(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrPCXDecode) || errors.Is(err, ErrDimensions) {
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
	return PCXFileResult{JPEG: encoded}, nil
}
