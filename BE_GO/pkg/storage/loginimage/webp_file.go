package loginimage

import (
	"context"
	"errors"
)

var ErrNotWebP = errors.New("login image requires another file decoder")

type WebPFileLimits struct {
	Codec                             JPEGLimits
	MaxDecodeBytes, MaxTransformBytes int64
}
type WebPFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}
type WebPFileProcessor struct {
	decoder *WebPDecoder
	encoder *JPEGEncoder
	limits  WebPFileLimits
	slots   chan struct{}
}

func NewWebPFileProcessor(path string, l WebPFileLimits) (*WebPFileProcessor, error) {
	if l.MaxTransformBytes <= 0 {
		return nil, ErrWebPLimits
	}
	d, err := NewWebPDecoder(path, WebPLimits{l.Codec.MaxEncodedBytes, l.MaxDecodeBytes, l.Codec.Concurrency, l.Codec.Timeout})
	if err != nil {
		return nil, err
	}
	e, err := NewJPEGEncoder(path, l.Codec)
	if err != nil {
		return nil, err
	}
	return &WebPFileProcessor{d, e, l, make(chan struct{}, l.Codec.Concurrency)}, nil
}
func (p *WebPFileProcessor) Process(ctx context.Context, b []byte) (WebPFileResult, error) {
	empty := WebPFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(b) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !webpSignature(b) {
		return empty, ErrNotWebP
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (WebPFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return WebPFileResult{KeepOriginal: true}, nil
	}
	h, err := p.decoder.Inspect(ctx, b)
	if errors.Is(err, ErrWebPDecode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	plan, err := Decide(h.Width, h.Height, len(b))
	if errors.Is(err, ErrDimensions) || plan.KeepOriginal {
		return original()
	}
	if err != nil {
		return empty, err
	}
	r, err := p.decoder.Decode(ctx, b)
	if errors.Is(err, ErrWebPDecode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if r.Width != h.Width || r.Height != h.Height || r.Mode != h.Mode {
		return empty, ErrWebPDecode
	}
	r, err = Transform(ctx, r, plan.Width, plan.Height, p.limits.MaxTransformBytes)
	if errors.Is(err, ErrDimensions) || errors.Is(err, ErrUnsupportedMode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	jpeg, err := p.encoder.Encode(ctx, r)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrDimensions) || errors.Is(err, ErrUnsupportedMode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return WebPFileResult{JPEG: jpeg}, nil
}
