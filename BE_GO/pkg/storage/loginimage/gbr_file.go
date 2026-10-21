package loginimage

import (
	"context"
	"errors"
)

var (
	ErrNotGBR        = errors.New("login image requires another file decoder")
	ErrGBRFileLimits = errors.New("login GBR file limits invalid")
)

type GBRFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes                    int
	MaxDecodeBytes, MaxTransformBytes int64
}
type GBRFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// GBRFileProcessor applies the lazy login-image policy to a selected GBR file.
// A shared instance bounds whole-pipeline admission and the request deadline.
// Header spans preserve grayscale comments; RGBA white composition drops them.
type GBRFileProcessor struct {
	encoder *JPEGEncoder
	limits  GBRFileLimits
	slots   chan struct{}
}

func NewGBRFileProcessor(path string, limits GBRFileLimits) (*GBRFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrGBRFileLimits
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &GBRFileProcessor{encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *GBRFileProcessor) Process(ctx context.Context, input []byte) (GBRFileResult, error) {
	empty := GBRFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !gbrSignature(input) {
		return empty, ErrNotGBR
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (GBRFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return GBRFileResult{KeepOriginal: true}, nil
	}
	h, err := ReadGBRHeader(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrGBRHeader) {
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
	decoded, err := DecodeGBR(ctx, h, input, p.limits.MaxDecodeBytes)
	if errors.Is(err, ErrGBRDecode) || errors.Is(err, ErrDimensions) {
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
	comment := input[h.CommentOffset : h.CommentOffset+h.CommentBytes]
	if h.Mode == "RGBA" {
		comment = nil
	}
	encoded, err := p.encoder.EncodeWithComment(ctx, raster, comment)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return GBRFileResult{JPEG: encoded}, nil
}
