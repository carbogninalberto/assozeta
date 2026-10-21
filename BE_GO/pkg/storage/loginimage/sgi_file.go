package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotSGI        = errors.New("login image requires another file decoder")
	ErrSGIFileLimits = errors.New("login SGI file limits invalid")
)

type SGIFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type SGIFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// SGIFileProcessor applies compress_base64's lazy admission and transform policy
// to SGI images. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type SGIFileProcessor struct {
	encoder *JPEGEncoder
	limits  SGIFileLimits
	slots   chan struct{}
}

func NewSGIFileProcessor(path string, limits SGIFileLimits) (*SGIFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrSGIFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &SGIFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *SGIFileProcessor) Process(ctx context.Context, input []byte) (SGIFileResult, error) {
	empty := SGIFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !sgiSignature(input) {
		return empty, ErrNotSGI
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (SGIFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return SGIFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadSGIHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrSGIHeader) {
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
	decoded, err := DecodeSGI(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrSGIDecode) || errors.Is(err, ErrDimensions) {
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
	return SGIFileResult{JPEG: encoded}, nil
}
