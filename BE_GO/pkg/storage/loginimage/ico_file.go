package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"math/bits"
)

var ErrNotICO = errors.New("login image requires another file decoder")
var ErrICODecode = errors.New("login ICO image invalid")

type ICOFileLimits PNGFileLimits
type ICOFileResult struct {
	KeepOriginal bool
	JPEG         []byte
}

// ICOFileProcessor follows Pillow's eager selected-frame load. ICO metadata is
// not copied from the embedded PNG/DIB. Budgets charge logical stage buffers,
// not allocator overhead or total RSS; one slot covers decoding and encoding.
type ICOFileProcessor struct {
	limits  ICOFileLimits
	decoder *PNGStreamDecoder
	encoder *JPEGEncoder
	slots   chan struct{}
}

func NewICOFileProcessor(path string, l ICOFileLimits) (*ICOFileProcessor, error) {
	if l.MaxHeaderBytes <= 0 || l.MaxTailBytes <= 0 || l.MaxDecodeBytes <= 0 || l.MaxTransformBytes <= 0 {
		return nil, ErrImageProcessorLimits
	}
	d, err := NewPNGStreamDecoder(path, PNGStreamLimits{l.Codec.MaxEncodedBytes, l.MaxDecodeBytes, l.Codec.Concurrency, l.Codec.Timeout})
	if err != nil {
		return nil, err
	}
	e, err := NewJPEGEncoder(path, l.Codec)
	if err != nil {
		return nil, err
	}
	return &ICOFileProcessor{l, d, e, make(chan struct{}, l.Codec.Concurrency)}, nil
}
func icoSignature(b []byte) bool { return bytes.HasPrefix(b, []byte{0, 0, 1, 0}) }

type icoEntry struct {
	offset, size               uint32
	bpp, area, depth, consumed int
}

// The two stable Pillow sorts amount to largest directory area followed by
// lowest color depth. Equal keys retain the first directory entry. Zero width
// or height means 256, and color-count one maps to the fallback depth 256.
func readICODirectory(ctx context.Context, b []byte, maxBytes int) (icoEntry, error) {
	empty := icoEntry{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 || min(len(b), 6) > maxBytes {
		return empty, ErrWorkingLimit
	}
	if len(b) < 6 || !icoSignature(b) {
		return empty, ErrICODecode
	}
	n := int(binary.LittleEndian.Uint16(b[4:]))
	if n == 0 {
		return empty, ErrICODecode
	}
	var selected icoEntry
	for i := 0; i < n; i++ {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		pos := 6 + i*16
		if min(len(b), pos+16) > maxBytes {
			return empty, ErrWorkingLimit
		}
		if pos > len(b)-16 {
			return empty, ErrICODecode
		}
		s := b[pos : pos+16]
		w, h := int(s[0]), int(s[1])
		if w == 0 {
			w = 256
		}
		if h == 0 {
			h = 256
		}
		e := icoEntry{offset: binary.LittleEndian.Uint32(s[12:]), size: binary.LittleEndian.Uint32(s[8:]), bpp: int(binary.LittleEndian.Uint16(s[6:])), area: w * h}
		e.depth = e.bpp
		if e.depth == 0 && s[2] > 1 {
			e.depth = bits.Len8(s[2] - 1)
		}
		if e.depth == 0 {
			e.depth = 256
		}
		if i == 0 || e.area > selected.area || e.area == selected.area && e.depth < selected.depth {
			selected = e
		}
	}
	selected.consumed = 6 + n*16
	return selected, nil
}

func (p *ICOFileProcessor) Process(ctx context.Context, b []byte) (ICOFileResult, error) {
	return p.processSize(ctx, b, 200)
}

func (p *ICOFileProcessor) processSize(ctx context.Context, b []byte, maxSize int) (ICOFileResult, error) {
	empty := ICOFileResult{}
	ctx, cancel := context.WithTimeout(ctx, p.limits.Codec.Timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(b) > p.limits.Codec.MaxEncodedBytes {
		return empty, ErrWorkingLimit
	}
	if !icoSignature(b) {
		return empty, ErrNotICO
	}
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	original := func() (ICOFileResult, error) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		return ICOFileResult{KeepOriginal: true}, nil
	}
	r, err := p.decode(ctx, b)
	if errors.Is(err, ErrICODecode) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	plan, err := decideSize(r.Width, r.Height, len(b), maxSize)
	if errors.Is(err, ErrDimensions) || plan.KeepOriginal {
		return original()
	}
	if err != nil {
		return empty, err
	}
	r, err = Transform(ctx, r, plan.Width, plan.Height, p.limits.MaxTransformBytes)
	if errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	jpeg, err := p.encoder.Encode(ctx, r)
	if errors.Is(err, ErrJPEGEncode) || errors.Is(err, ErrUnsupportedMode) || errors.Is(err, ErrDimensions) {
		return original()
	}
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return ICOFileResult{JPEG: jpeg}, nil
}

func (p *ICOFileProcessor) decode(ctx context.Context, b []byte) (r Raster, err error) {
	defer func() {
		if errors.Is(err, ErrBMPHeader) || errors.Is(err, ErrBMPDecode) || errors.Is(err, ErrPNGHeader) || errors.Is(err, ErrPNGDecode) || errors.Is(err, ErrDimensions) {
			r = Raster{}
			err = ErrICODecode
		}
	}()
	if len(b) > p.limits.Codec.MaxEncodedBytes {
		return Raster{}, ErrWorkingLimit
	}
	e, err := readICODirectory(ctx, b, p.limits.MaxHeaderBytes)
	if err != nil {
		return Raster{}, err
	}
	if uint64(e.offset) >= uint64(len(b)) {
		return Raster{}, ErrICODecode
	}
	offset := int(e.offset)
	frame := b[offset:]
	remaining := p.limits.MaxHeaderBytes - e.consumed
	if bytes.HasPrefix(frame, []byte("\x89PNG\r\n\x1a\n")) {
		return p.decodePNG(ctx, b, offset, remaining)
	}
	h, err := ReadBMPHeader(ctx, frame, remaining)
	if err != nil {
		return Raster{}, err
	}
	if h.Format != "DIB" {
		return Raster{}, ErrICODecode
	}
	h.Height /= 2
	r, err = DecodeBMP(ctx, h, frame, p.limits.MaxDecodeBytes-int64(offset))
	if err != nil {
		return Raster{}, err
	}
	return icoAlpha(ctx, b, e, h, r, p.limits.MaxDecodeBytes)
}

func (p *ICOFileProcessor) decodePNG(ctx context.Context, b []byte, offset, headerLimit int) (Raster, error) {
	s, err := readPNGHeaderState(ctx, b[offset:], headerLimit)
	if err != nil {
		return Raster{}, err
	}
	h := s.header
	if h.InvalidTile || h.DataKind == "IEND" || h.DataOffset == len(b)-offset {
		return Raster{}, ErrICODecode
	}
	if h.Tile.X == 0 && h.Tile.Width == 0 {
		h.Tile = PNGRect{Width: h.Width, Height: h.Height}
	}
	t := h.Tile
	if t.Width <= 0 || t.Height <= 0 || t.X < 0 || t.Y < 0 || t.X > h.Width-t.Width || t.Y > h.Height-t.Height {
		return Raster{}, ErrICODecode
	}
	spec := PNGStreamSpec{PNGPixelSpec{t.Width, t.Height, h.Depth, h.Color, h.Interlace}, h.DataOffset + offset, h.DataLength, s.sequence}
	decoded, err := p.decoder.Decode(ctx, b, spec)
	if err != nil {
		return Raster{}, err
	}
	if _, err = readPNGTail(ctx, b, s, decoded, p.limits.MaxTailBytes); err != nil {
		return Raster{}, err
	}
	return pngCanvas(ctx, h, decoded.Raster, p.limits.MaxDecodeBytes-int64(len(b)))
}

func icoAlpha(ctx context.Context, b []byte, e icoEntry, h BMPHeader, r Raster, maxBytes int64) (Raster, error) {
	empty := Raster{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	size, err := rasterBytes(h.Width, h.Height, 4)
	if err != nil {
		return empty, err
	}
	if maxBytes <= 0 || int64(len(b)) > maxBytes || int64(len(r.Pixels)) > maxBytes-int64(len(b)) || size > maxBytes-int64(len(b))-int64(len(r.Pixels)) {
		return empty, ErrWorkingLimit
	}
	maskOffset, maskStride := int64(e.offset)+int64(h.DataOffset), int64(h.Width)*4
	rowBytes := maskStride
	if e.bpp != 32 {
		maskStride = ((int64(h.Width) + 31) / 32) * 4
		rowBytes = (int64(h.Width) + 7) / 8
		maskOffset = int64(e.offset) + int64(e.size) - maskStride*int64(h.Height)
	}
	if maskOffset < 0 || maskOffset > int64(len(b)) || rowBytes > int64(len(b))-maskOffset || int64(h.Height-1) > (int64(len(b))-maskOffset-rowBytes)/maskStride {
		return empty, ErrICODecode
	}
	var palette []byte
	padding := 4
	if r.Mode == "P" {
		frame := b[int(e.offset):]
		n := int(binary.LittleEndian.Uint32(frame))
		pos := n
		if n == 12 {
			padding = 3
		} else if h.Compression == 3 && n < 52 {
			pos += 12
		}
		if pos > h.DataOffset || h.DataOffset > len(frame) {
			return empty, ErrICODecode
		}
		palette = frame[pos:h.DataOffset]
	}
	out := make([]byte, int(size))
	channels := 1
	if r.Mode == "RGB" {
		channels = 3
	} else if r.Mode == "RGBA" {
		channels = 4
	}
	for y := 0; y < h.Height; y++ {
		mask := b[int(maskOffset+int64(h.Height-1-y)*maskStride):]
		for x := 0; x < h.Width; x++ {
			if x&4095 == 0 {
				if err := ctx.Err(); err != nil {
					return empty, err
				}
			}
			i := y*h.Width + x
			dst := out[i*4 : i*4+4]
			src := r.Pixels[i*channels:]
			switch r.Mode {
			case "RGB", "RGBA":
				copy(dst, src[:3])
			case "L", "1":
				dst[0], dst[1], dst[2] = src[0], src[0], src[0]
			case "P":
				index := int(src[0]) * padding
				if index+3 <= len(palette) {
					dst[0], dst[1], dst[2] = palette[index+2], palette[index+1], palette[index]
				}
			default:
				return empty, ErrICODecode
			}
			if e.bpp == 32 {
				dst[3] = mask[x*4+3]
			} else {
				dst[3] = 255 * (1 - ((mask[x/8] >> uint(7-x%8)) & 1))
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return Raster{h.Width, h.Height, "RGBA", out}, nil
}
