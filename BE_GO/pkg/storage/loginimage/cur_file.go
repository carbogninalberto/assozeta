package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotCUR        = errors.New("login image requires another file decoder")
	ErrCURFileLimits = errors.New("login CUR file limits invalid")
)

type CURFileLimits = BMPFileLimits
type CURFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// CURFileProcessor applies compress_base64's lazy admission and transform policy
// to Windows CUR with lazy DIB decoding. Share an instance to bound all active
// requests across the entire pipeline. Stage budgets account for logical buffers, not aggregate RSS.
type CURFileProcessor struct {
	encoder *JPEGEncoder
	limits  CURFileLimits
	slots   chan struct{}
}

func NewCURFileProcessor(path string, limits CURFileLimits) (*CURFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrCURFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &CURFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *CURFileProcessor) Process(ctx context.Context, input []byte) (CURFileResult, error) {
	empty := CURFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !curSignature(input) {
		return empty, ErrNotCUR
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (CURFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return CURFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadCURHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrCURHeader) {
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
	decoded, err := DecodeBMP(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrBMPDecode) || errors.Is(err, ErrDimensions) {
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
	return CURFileResult{JPEG: encoded}, nil
}
