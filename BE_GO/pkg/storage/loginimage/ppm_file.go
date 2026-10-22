package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotPPM        = errors.New("login image requires another file decoder")
	ErrPPMFileLimits = errors.New("login PPM file limits invalid")
)

type PPMFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type PPMFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// PPMFileProcessor applies compress_base64's lazy admission and transform policy
// to PBM/PGM/PPM/PFM and PIL extensions. Share an instance to bound the entire pipeline's active
// requests. Stage budgets account for logical buffers, not aggregate RSS.
type PPMFileProcessor struct {
	encoder *JPEGEncoder
	limits  PPMFileLimits
	slots   chan struct{}
}

func NewPPMFileProcessor(path string, limits PPMFileLimits) (*PPMFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrPPMFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &PPMFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *PPMFileProcessor) Process(ctx context.Context, input []byte) (PPMFileResult, error) {
	return p.processSize(ctx, input, 200)
}

func (p *PPMFileProcessor) processSize(ctx context.Context, input []byte, maxSize int) (PPMFileResult, error) {
	empty := PPMFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !ppmSignature(input) {
		return empty, ErrNotPPM
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (PPMFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return PPMFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadPPMHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrPPMHeader) {
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
	decoded, err := DecodePPM(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrPPMDecode) || errors.Is(err, ErrDimensions) {
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
	return PPMFileResult{JPEG: encoded}, nil
}
