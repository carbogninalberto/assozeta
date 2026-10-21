package loginimage

import (
	"context"
	"errors"
)

var ErrNotJPEG = errors.New("login image requires another file decoder")

// JPEGFileLimits bounds the complete JPEG pipeline. Memory limits account for
// logical buffers, not allocator overhead or the native codec's total RSS.
type JPEGFileLimits struct {
	Codec             JPEGLimits
	MaxHeaderBytes    int
	MaxTransformBytes int64
}

type JPEGFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// JPEGFileProcessor implements compress_base64's JPEG/MPO first-frame path.
// Share one instance across requests to bound admission for all its stages.
// Other formats require another processor; this is not a data-URI dispatcher.
type JPEGFileProcessor struct {
	decoder *JPEGDecoder
	encoder *JPEGEncoder
	limits  JPEGFileLimits
	slots   chan struct{}
}

func NewJPEGFileProcessor(path string, limits JPEGFileLimits) (*JPEGFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrJPEGLimits
	}
	decoder, err := NewJPEGDecoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &JPEGFileProcessor{decoder, encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}

func (p *JPEGFileProcessor) Process(ctx context.Context, input []byte) (JPEGFileResult, error) {
	empty := JPEGFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if len(input) < 3 || input[0] != 255 || input[1] != 216 || input[2] != 255 {
		return empty, ErrNotJPEG
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (JPEGFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return JPEGFileResult{KeepOriginal: true}, nil
	}
	info, err := InspectJPEG(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrJPEGHeader) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	plan, err := Decide(info.Header.Width, info.Header.Height, len(input))
	if errors.Is(err, ErrDimensions) || plan.KeepOriginal {
		return original()
	}
	if err != nil {
		return empty, err
	}
	decoded, err := p.decoder.Decode(ctx, input)
	if errors.Is(err, ErrJPEGDecode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	raster, err := Transform(ctx, decoded.Raster, plan.Width, plan.Height, p.limits.MaxTransformBytes)
	if err != nil {
		return empty, err
	}
	// Image.open owns the inherited comment, including tables-only streams.
	encoded, err := p.encoder.EncodeWithComment(ctx, raster, info.Header.Comment)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return JPEGFileResult{JPEG: encoded}, nil
}
