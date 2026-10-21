package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotSUN        = errors.New("login image requires another file decoder")
	ErrSUNFileLimits = errors.New("login SUN file limits invalid")
)

type SUNFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type SUNFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// SUNFileProcessor applies compress_base64's lazy admission and transform policy
// to SUN images selected by the caller. Signature matching alone does not
// implement Pillow's registry precedence (some candidates first match GBR).
// Share an instance to bound active requests across the entire pipeline. Stage
// budgets account for logical buffers, not aggregate RSS.
type SUNFileProcessor struct {
	encoder *JPEGEncoder
	limits  SUNFileLimits
	slots   chan struct{}
}

func NewSUNFileProcessor(path string, limits SUNFileLimits) (*SUNFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrSUNFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &SUNFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *SUNFileProcessor) Process(ctx context.Context, input []byte) (SUNFileResult, error) {
	empty := SUNFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !sunSignature(input) {
		return empty, ErrNotSUN
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (SUNFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return SUNFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadSUNHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrSUNHeader) {
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
	decoded, err := DecodeSUN(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrSUNDecode) || errors.Is(err, ErrDimensions) {
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
	return SUNFileResult{JPEG: encoded}, nil
}
