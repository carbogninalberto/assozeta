package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotTGA        = errors.New("login image requires another file decoder")
	ErrTGAFileLimits = errors.New("login TGA file limits invalid")
)

type TGAFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type TGAFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// TGAFileProcessor applies compress_base64's lazy admission and transform policy
// to raw and RLE TGA. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type TGAFileProcessor struct {
	encoder *JPEGEncoder
	limits  TGAFileLimits
	slots   chan struct{}
}

func NewTGAFileProcessor(path string, limits TGAFileLimits) (*TGAFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrTGAFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &TGAFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *TGAFileProcessor) Process(ctx context.Context, input []byte) (TGAFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *TGAFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (TGAFileResult, error) {
	empty := TGAFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !tgaSignature(input) {
		return empty, ErrNotTGA
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (TGAFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return TGAFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadTGAHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrTGAHeader) {
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
	decoded, err := DecodeTGA(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrTGADecode) || errors.Is(err, ErrDimensions) {
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
	return TGAFileResult{JPEG: encoded}, nil
}
