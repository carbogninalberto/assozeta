package loginimage

import (
	"bytes"
	"context"
	"errors"
)

var ErrNotPNG = errors.New("login image requires another file decoder")

type PNGFileLimits struct {
	Codec                             JPEGLimits
	MaxHeaderBytes, MaxTailBytes      int
	MaxDecodeBytes, MaxTransformBytes int64
}
type PNGFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// PNGFileProcessor composes lazy admission, first-frame pixels, post-pixel
// metadata, canvas placement and compress_base64's transform/JPEG policy.
// Share one instance for whole-pipeline concurrency/deadline admission.
// Limits account for logical stage buffers; they do not cap aggregate RSS.
type PNGFileProcessor struct {
	decoder *PNGStreamDecoder
	encoder *JPEGEncoder
	limits  PNGFileLimits
	slots   chan struct{}
}

func NewPNGFileProcessor(path string, limits PNGFileLimits) (*PNGFileProcessor, error) {
	if limits.MaxHeaderBytes <= 0 || limits.MaxTailBytes <= 0 || limits.MaxDecodeBytes <= 0 || limits.MaxTransformBytes <= 0 {
		return nil, ErrPNGStreamLimits
	}
	decoder, err := NewPNGStreamDecoder(path, PNGStreamLimits{limits.Codec.MaxEncodedBytes, limits.MaxDecodeBytes, limits.Codec.Concurrency, limits.Codec.Timeout})
	if err != nil {
		return nil, err
	}
	encoder, err := NewJPEGEncoder(path, limits.Codec)
	if err != nil {
		return nil, err
	}
	return &PNGFileProcessor{decoder, encoder, limits, make(chan struct{}, limits.Codec.Concurrency)}, nil
}
func (p *PNGFileProcessor) Process(ctx context.Context, input []byte) (PNGFileResult, error) {
	empty := PNGFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(input) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if len(input) < 8 || !bytes.Equal(input[:8], []byte("\x89PNG\r\n\x1a\n")) {
		return empty, ErrNotPNG
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (PNGFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return PNGFileResult{KeepOriginal: true}, nil
	}
	state, err := readPNGHeaderState(ctx, input, p.limits.MaxHeaderBytes)
	if errors.Is(err, ErrPNGHeader) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	h := state.header
	plan, err := Decide(h.Width, h.Height, len(input))
	if errors.Is(err, ErrDimensions) || plan.KeepOriginal {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if h.InvalidTile || h.DataKind == "IEND" || h.DataOffset == len(input) {
		return original()
	}
	// Pillow's decoder.setimage interprets x0=x1=0 as the full image rectangle.
	if h.Tile.X == 0 && h.Tile.Width == 0 {
		h.Tile = PNGRect{Width: h.Width, Height: h.Height}
	}
	tile := h.Tile
	if tile.Width <= 0 || tile.Height <= 0 || tile.X < 0 || tile.Y < 0 || tile.X > h.Width-tile.Width || tile.Y > h.Height-tile.Height {
		return original()
	}
	spec := PNGStreamSpec{PNGPixelSpec{tile.Width, tile.Height, h.Depth, h.Color, h.Interlace}, h.DataOffset, h.DataLength, state.sequence}
	decoded, err := p.decoder.Decode(ctx, input, spec)
	if errors.Is(err, ErrPNGDecode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	state, err = readPNGTail(ctx, input, state, decoded, p.limits.MaxTailBytes)
	if errors.Is(err, ErrPNGHeader) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	canvas, err := pngCanvas(ctx, h, decoded.Raster, p.limits.MaxDecodeBytes)
	if err != nil {
		return empty, err
	}
	raster, err := Transform(ctx, canvas, plan.Width, plan.Height, p.limits.MaxTransformBytes)
	if errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	comment := state.header.Comment
	// Django creates a fresh white RGB image for RGBA; that drops all metadata.
	if h.Mode == "RGBA" {
		comment = nil
	}
	encoded, err := p.encoder.EncodeWithComment(ctx, raster, comment)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrDimensions) || errors.Is(err, ErrUnsupportedMode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return PNGFileResult{JPEG: encoded}, nil
}

func pngCanvas(ctx context.Context, h PNGHeader, input Raster, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	mode, _, channels := pngPixelMode(h.Depth, h.Color)
	if mode == "" || input.Mode != mode {
		return empty, ErrPNGDecode
	}
	tile := h.Tile
	if tile.Width != input.Width || tile.Height != input.Height || tile.X < 0 || tile.Y < 0 || tile.Width <= 0 || tile.Height <= 0 || tile.X > h.Width-tile.Width || tile.Y > h.Height-tile.Height {
		return empty, ErrPNGDecode
	}
	size, err := rasterBytes(h.Width, h.Height, channels)
	if err != nil {
		return empty, err
	}
	source, err := rasterBytes(input.Width, input.Height, channels)
	if err != nil {
		return empty, err
	}
	if int64(len(input.Pixels)) != source {
		return empty, ErrPNGDecode
	}
	if maxBytes <= 0 || source > maxBytes {
		return empty, ErrWorkingLimit
	}
	if tile.X == 0 && tile.Y == 0 && tile.Width == h.Width && tile.Height == h.Height {
		return input, nil
	}
	if size > maxBytes-source {
		return empty, ErrWorkingLimit
	}
	output := make([]byte, int(size))
	// Pillow 11.1 ZipDecode's Adam7 branch does not add xoff/yoff when
	// shuffling into the image; retain its observable first-frame placement.
	x0, y0 := tile.X, tile.Y
	if h.Interlace {
		x0, y0 = 0, 0
	}
	rowBytes := tile.Width * channels
	for y := 0; y < tile.Height; y++ {
		for x := 0; x < rowBytes; x += 65536 {
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			n := min(65536, rowBytes-x)
			copy(output[((y0+y)*h.Width+x0)*channels+x:][:n], input.Pixels[y*rowBytes+x:][:n])
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, mode, output}, nil
}
