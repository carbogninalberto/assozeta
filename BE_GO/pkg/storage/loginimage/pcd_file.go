package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotPCD        = errors.New("login image requires another file decoder")
	ErrPCDFileLimits = errors.New("login PCD file limits invalid")
)

type PCDFileLimits = BMPFileLimits
type PCDFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// PCDFileProcessor applies compress_base64's lazy admission and transform policy
// to Kodak PhotoCD. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type PCDFileProcessor struct {
	encoder *JPEGEncoder
	limits  PCDFileLimits
	slots   chan struct{}
}

func NewPCDFileProcessor(path string, limits PCDFileLimits) (*PCDFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrPCDFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &PCDFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *PCDFileProcessor) Process(ctx context.Context, input []byte) (PCDFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *PCDFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (PCDFileResult, error) {
	empty := PCDFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !pcdSignature(input) {
		return empty, ErrNotPCD
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (PCDFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return PCDFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadPCDHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrPCDHeader) {
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
	decoded, err := DecodePCD(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrPCDDecode) || errors.Is(err, ErrDimensions) {
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
	return PCDFileResult{JPEG: encoded}, nil
}
