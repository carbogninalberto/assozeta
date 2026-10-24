package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotQOI        = errors.New("login image requires another file decoder")
	ErrQOIFileLimits = errors.New("login QOI file limits invalid")
)

type QOIFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type QOIFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// QOIFileProcessor applies compress_base64's lazy admission and transform policy
// to QOI images. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type QOIFileProcessor struct {
	encoder *JPEGEncoder
	limits  QOIFileLimits
	slots   chan struct{}
}

func NewQOIFileProcessor(path string, limits QOIFileLimits) (*QOIFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrQOIFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &QOIFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *QOIFileProcessor) Process(ctx context.Context, input []byte) (QOIFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *QOIFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (QOIFileResult, error) {
	empty := QOIFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !qoiSignature(input) {
		return empty, ErrNotQOI
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (QOIFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return QOIFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadQOIHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrQOIHeader) {
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
	decoded, err := DecodeQOI(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrQOIDecode) || errors.Is(err, ErrDimensions) {
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
	return QOIFileResult{JPEG: encoded}, nil
}
