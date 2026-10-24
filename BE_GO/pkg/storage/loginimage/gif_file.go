package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotGIF        = errors.New("login image requires another file decoder")
	ErrGIFFileLimits = errors.New("login GIF file limits invalid")
)

type GIFFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type GIFFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// GIFFileProcessor applies compress_base64's lazy admission and transform policy
// to GIF images. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type GIFFileProcessor struct {
	encoder *JPEGEncoder
	limits  GIFFileLimits
	slots   chan struct{}
}

func NewGIFFileProcessor(path string, limits GIFFileLimits) (*GIFFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrGIFFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &GIFFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *GIFFileProcessor) Process(ctx context.Context, input []byte) (GIFFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *GIFFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (GIFFileResult, error) {
	empty := GIFFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !gifSignature(input) {
		return empty, ErrNotGIF
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (GIFFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return GIFFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadGIFHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrGIFHeader) {
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
	decoded, err := DecodeGIF(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrGIFDecode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if h.PaletteAttached {
		return original()
	}
	raster, err := Transform(ctx, decoded, plan.Width, plan.Height, p.limits.MaxTransformBytes)
	if errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	encoded, err := p.encoder.EncodeWithComment(ctx, raster, h.Comment)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return GIFFileResult{JPEG: encoded}, nil
}
