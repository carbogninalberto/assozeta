package loginimage

import (
	"context"
	"encoding/base64"
	"errors"
)

var ErrImageProcessorLimits = errors.New("invalid login image processor limits")
var ErrImageDecoderUnavailable = errors.New("login image format decoding is incomplete")
var errImageResult = errors.New("invalid login image file result")

// ProcessorLimits bounds data-URI input/output and each file stage. The global
// Codec.Concurrency and Codec.Timeout apply before Base64 decoding and across all
// formats. Limits charge logical payloads, not total RSS or queued caller inputs.
type ProcessorLimits struct {
	Codec                                     JPEGLimits
	MaxURIBytes, MaxHeaderBytes, MaxTailBytes int
	MaxDecodeBytes, MaxTransformBytes         int64
}
type imageFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}
type imageFormat struct {
	rejected, terminal error
	name               string
	accepts            func([]byte) bool
	probe              func(context.Context, []byte) error
	process            func(context.Context, []byte, int) (imageFileResult, error)
}

// Processor composes implemented file pipelines with one admission/deadline.
// Missing codecs and unresolved ambiguous headers fail explicitly. It is not yet
// a complete replacement for Pillow's registry; public login propagates its gaps.
type Processor struct {
	limits  ProcessorLimits
	formats []imageFormat
	slots   chan struct{}
}

func NewProcessor(path string, limits ProcessorLimits) (*Processor, error) {
	if limits.MaxURIBytes <= len(jpegDataPrefix) || limits.MaxHeaderBytes <= 0 || limits.MaxTailBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrImageProcessorLimits
	}
	formats, err := newImageFormats(path, limits)
	if err != nil {
		return nil, err
	}
	return &Processor{limits, formats, make(chan struct{}, limits.Codec.Concurrency)}, nil
}

const jpegDataPrefix = "data:image/jpeg;base64,"

func (p *Processor) CompressLoginImage(ctx context.Context, value string) (string, error) {
	return p.compress(ctx, value, 200)
}

// CompressProfileImage applies the avatar-upload variant of compress_base64.
// Uploads and presentation share the same admission pool, codecs and budgets.
// The 512-pixel bound applies only above the helper's strict 1 MiB threshold.
func (p *Processor) CompressProfileImage(ctx context.Context, value string) (string, error) {
	return p.compress(ctx, value, 512)
}

func (p *Processor) compress(ctx context.Context, value string, maxSize int) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if len(value) > p.limits.MaxURIBytes {
		return "", ErrWorkingLimit
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return "", ctx.Err()
	}
	original := func() (string, error) {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		return value, nil
	}
	raw, err := DecodeDataURI(ctx, value, p.limits.Codec.MaxEncodedBytes)
	if errors.Is(err, ErrDataURI) {
		return original()
	}
	if err != nil {
		return "", err
	}
	result, err := p.processFile(ctx, raw, maxSize)
	if err != nil {
		return "", err
	}
	if result.KeepOriginal {
		if len(result.JPEG) != 0 {
			return "", errImageResult
		}
		return original()
	}
	if len(result.JPEG) == 0 {
		return "", errImageResult
	}
	groups := len(result.JPEG) / 3
	if len(result.JPEG)%3 != 0 {
		groups++
	}
	if groups > (p.limits.MaxURIBytes-len(jpegDataPrefix))/4 {
		return "", ErrWorkingLimit
	}
	output := make([]byte, len(jpegDataPrefix)+groups*4)
	copy(output, jpegDataPrefix)
	for i := 0; i < len(result.JPEG); {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		n := min(3*4096, len(result.JPEG)-i)
		base64.StdEncoding.Encode(output[len(jpegDataPrefix)+i/3*4:], result.JPEG[i:i+n])
		i += n
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	return string(output), nil
}
func (p *Processor) processFile(ctx context.Context, input []byte, maxSize int) (imageFileResult, error) {
	matches := make([]*imageFormat, 0, 4)
	for i := range p.formats {
		if err := ctx.Err(); err != nil {
			return imageFileResult{}, err
		}
		if p.formats[i].accepts(input) {
			matches = append(matches, &p.formats[i])
		}
	}
	for i, first := range matches {
		if first.process == nil {
			return imageFileResult{}, ErrImageDecoderUnavailable
		}
		if i+1 < len(matches) {
			if err := first.probe(ctx, input); err != nil {
				if ctx.Err() != nil {
					return imageFileResult{}, ctx.Err()
				}
				if errors.Is(err, ErrWorkingLimit) {
					return imageFileResult{}, err
				}
				if first.terminal != nil && errors.Is(err, first.terminal) {
					return imageFileResult{KeepOriginal: true}, nil
				}
				if first.rejected != nil && errors.Is(err, first.rejected) {
					continue
				}
				// Other readers still collapse recoverable/terminal opening exceptions.
				return imageFileResult{}, ErrImageDecoderUnavailable
			}
		}
		return first.process(ctx, input, maxSize)
	}
	return imageFileResult{KeepOriginal: true}, nil
}
