package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotBMP        = errors.New("login image requires another file decoder")
	ErrBMPFileLimits = errors.New("login BMP file limits invalid")
)

type BMPFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type BMPFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// BMPFileProcessor applies compress_base64's lazy admission and transform policy
// to standalone BMP/DIB. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type BMPFileProcessor struct {
	encoder *JPEGEncoder
	limits  BMPFileLimits
	slots   chan struct{}
}

func NewBMPFileProcessor(path string, limits BMPFileLimits) (*BMPFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrBMPFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &BMPFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *BMPFileProcessor) Process(ctx context.Context, input []byte) (BMPFileResult, error) {
	empty := BMPFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !bmpSignature(input) {
		return empty, ErrNotBMP
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (BMPFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return BMPFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadBMPHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrBMPHeader) {
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
	return BMPFileResult{JPEG: encoded}, nil
}
