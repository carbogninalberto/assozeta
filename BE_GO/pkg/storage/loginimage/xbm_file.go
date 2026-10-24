package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotXBM        = errors.New("login image requires another file decoder")
	ErrXBMFileLimits = errors.New("login XBM file limits invalid")
)

type XBMFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type XBMFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// XBMFileProcessor applies compress_base64's lazy admission and transform policy
// to XBM images. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type XBMFileProcessor struct {
	encoder *JPEGEncoder
	limits  XBMFileLimits
	slots   chan struct{}
}

func NewXBMFileProcessor(path string, limits XBMFileLimits) (*XBMFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrXBMFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &XBMFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *XBMFileProcessor) Process(ctx context.Context, input []byte) (XBMFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *XBMFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (XBMFileResult, error) {
	empty := XBMFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !xbmSignature(input) {
		return empty, ErrNotXBM
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (XBMFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return XBMFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadXBMHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrXBMHeader) {
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
	decoded, err := DecodeXBM(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrXBMDecode) || errors.Is(err, ErrDimensions) {
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
	return XBMFileResult{JPEG: encoded}, nil
}
