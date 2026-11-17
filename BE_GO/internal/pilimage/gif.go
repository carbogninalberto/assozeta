package pilimage

import (
	"bytes"
	"encoding/binary"
)

// gifState holds the first frame's tile and LZW parameters.
type gifState struct {
	minCodeSize int
	interlace   bool
	x0, y0      int
	x1, y1      int
	offset      int // start of the LZW data (after the minimum code size byte)
}

func acceptGIF(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("GIF87a")) || bytes.HasPrefix(prefix, []byte("GIF89a"))
}

// gifData is GifImageFile.data: read a sub-block length then that many bytes.
// present is false when the length byte is zero (Python's None); a non-zero
// length with no bytes left gives an empty, present block.
func gifData(data []byte, pos *int) (block []byte, present bool) {
	if *pos >= len(data) {
		return nil, false
	}
	n := int(data[*pos])
	*pos++
	if n == 0 {
		return nil, false
	}
	end := min(*pos+n, len(data))
	out := data[*pos:end]
	*pos = end
	return out, true
}

// gifPaletteNeeded is GifImageFile._is_palette_needed. A colour table whose
// length is not a multiple of three raises IndexError, which makes Pillow skip
// the plugin.
func gifPaletteNeeded(p []byte) (needed bool, ok bool) {
	if len(p)%3 != 0 {
		return false, false
	}
	for i := 0; i < len(p); i += 3 {
		if !(i/3 == int(p[i]) && p[i] == p[i+1] && p[i] == p[i+2]) {
			return true, true
		}
	}
	return false, true
}

// openGIF is GifImageFile._open followed by its first _seek(0).
func openGIF(data []byte) (*Image, error) {
	if len(data) < 6 {
		return nil, syntaxError("not a GIF file")
	}
	if !acceptGIF(data[:6]) {
		return nil, syntaxError("not a GIF file")
	}
	if len(data) < 13 {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	im := newImage(data, "GIF", "", int(binary.LittleEndian.Uint16(data[6:])), int(binary.LittleEndian.Uint16(data[8:])))
	flags := data[10]
	bits := int(flags&7) + 1
	var globalPalette []byte
	if flags&128 != 0 {
		// info["background"] = s[11]; the palette follows.
		pos := 13
		end := min(pos+3<<bits, len(data))
		p := data[pos:end]
		needed, ok := gifPaletteNeeded(p)
		if !ok {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		if needed {
			globalPalette = p
		}
	}
	pos := 13
	if flags&128 != 0 {
		pos += 3 << bits
		pos = min(pos, len(data))
	}

	// _seek(0).
	if pos >= len(data) || data[pos] == ';' {
		return nil, &Error{Class: "EOFError", Text: "no more images in GIF file"}
	}
	var (
		frameTransparency *int
		transparencySet   bool
		localPalette      []byte
		localPaletteSet   bool
		interlace         bool
		interlaceSet      bool
	)
	for {
		if pos >= len(data) {
			break
		}
		s := data[pos]
		pos++
		if s == ';' {
			break
		}
		if s == '!' {
			if pos >= len(data) {
				return nil, &Error{Class: "IndexError", Text: "index out of range"}
			}
			kind := data[pos]
			pos++
			block, present := gifData(data, &pos)
			switch kind {
			case 249: // graphic control extension
				if present {
					if len(block) < 1 {
						return nil, &Error{Class: "IndexError", Text: "index out of range"}
					}
					gflags := block[0]
					if gflags&1 != 0 {
						if len(block) < 4 {
							return nil, &Error{Class: "IndexError", Text: "index out of range"}
						}
						t := int(block[3])
						frameTransparency = &t
						transparencySet = true
					}
					if len(block) < 3 {
						return nil, shortError()
					}
				}
			case 254: // comment
				for present && len(block) > 0 {
					block, present = gifData(data, &pos)
				}
				continue
			case 255: // application
				if present && bytes.HasPrefix(block, []byte("NETSCAPE2.0")) {
					// The loop-count sub-block is read once here; the rest is
					// consumed by the loop below.
					gifData(data, &pos)
				}
			}
			for {
				b, ok := gifData(data, &pos)
				if !ok || len(b) == 0 {
					break
				}
			}
			continue
		}
		if s == ',' {
			if pos+9 > len(data) {
				return nil, &Error{Class: "IndexError", Text: "index out of range"}
			}
			sub := data[pos : pos+9]
			pos += 9
			x0 := int(binary.LittleEndian.Uint16(sub[0:]))
			y0 := int(binary.LittleEndian.Uint16(sub[2:]))
			x1 := x0 + int(binary.LittleEndian.Uint16(sub[4:]))
			y1 := y0 + int(binary.LittleEndian.Uint16(sub[6:]))
			if x1 > im.Width || y1 > im.Height {
				w, h := max(x1, im.Width), max(y1, im.Height)
				if err := checkDecompressionBomb(w, h); err != nil {
					return nil, err
				}
				im.Width, im.Height = w, h
			}
			flags := sub[8]
			interlace = flags&64 != 0
			interlaceSet = true
			if flags&128 != 0 {
				n := 3 << ((flags & 7) + 1)
				end := min(pos+n, len(data))
				p := data[pos:end]
				needed, ok := gifPaletteNeeded(p)
				if !ok {
					return nil, &Error{Class: "IndexError", Text: "index out of range"}
				}
				if needed {
					localPalette = p
				} else {
					localPalette = nil
				}
				localPaletteSet = true
				pos = end
			}
			if pos >= len(data) {
				return nil, &Error{Class: "IndexError", Text: "index out of range"}
			}
			minCodeSize := int(data[pos])
			pos++
			gif := &gifState{
				minCodeSize: minCodeSize,
				interlace:   interlace,
				x0:          x0, y0: y0, x1: x1, y1: y1,
				offset: pos,
			}
			im.gif = gif
			// Mode and palette.
			usePalette := localPalette
			if !localPaletteSet {
				usePalette = globalPalette
			}
			if len(usePalette) > 0 {
				im.Mode = "P"
			} else {
				im.Mode = "L"
			}
			if localPaletteSet && localPalette != nil {
				im.setPaletteRGB(localPalette, localPalette)
			} else if len(globalPalette) > 0 {
				im.setPaletteRGB(globalPalette, globalPalette)
			}
			if transparencySet && frameTransparency != nil {
				im.transparency = transparency{present: true, isInt: true, index: *frameTransparency}
			}
			return im, nil
		}
	}
	if !interlaceSet {
		return nil, &Error{Class: "EOFError", Text: "image not found in GIF frame"}
	}
	return nil, &Error{Class: "EOFError", Text: "image not found in GIF frame"}
}

// gifDecoder is the GIFDECODERSTATE of src/libImaging/GifDecode.c.
type gifDecoder struct {
	bits         int
	interlace    int
	transparency int

	step, repeat int
	bitbuffer    uint32
	bitcount     int
	blocksize    int
	codesize     int
	codemask     int
	clear, end   int
	lastcode     int
	lastdata     byte
	bufferindex  int
	buffer       [gifTable]byte
	link         [gifTable]uint16
	data         [gifTable]byte
	next         int
}

const (
	gifBits   = 12
	gifTable  = 1 << gifBits
	gifBuffer = 1 << gifBits
)

// loadGIF runs ImagingGifDecode over the tile's data.
func (im *Image) loadGIF() error {
	g := im.gif
	if g == nil {
		return osError("cannot load this image")
	}
	// Image._getdecoder -> setimage validates the tile extents before the
	// decoder looks at its configuration.
	if g.x0 < 0 || g.y0 < 0 || g.x1 <= g.x0 || g.y1 <= g.y0 || g.x1 > im.Width || g.y1 > im.Height {
		return valueError("tile cannot extend outside image")
	}
	if g.minCodeSize < 0 || g.minCodeSize > 12 {
		return decoderError(-8)
	}
	im.pix = make([]byte, im.Width*im.Height)
	d := &gifDecoder{bits: g.minCodeSize, transparency: -1}
	if g.interlace {
		d.interlace = 1
		d.step = 8
		d.repeat = 8
	} else {
		d.step = 1
	}
	d.clear = 1 << d.bits
	d.end = d.clear + 1
	d.next = d.clear + 2
	d.codesize = d.bits + 1
	d.codemask = (1 << d.codesize) - 1
	d.bufferindex = gifBuffer

	stream := im.data
	ptr := g.offset
	if ptr > len(stream) {
		ptr = len(stream)
	}
	xsize := g.x1 - g.x0
	ysize := g.y1 - g.y0
	x, y := 0, 0
	if d.interlace == 1 {
		y = 0
	}
	state := 2

	// newLine is the NEWLINE macro; it reports that the image is complete.
	newLine := func() bool {
		x = 0
		y += d.step
		for y >= ysize {
			switch d.interlace {
			case 1:
				d.repeat = 4
				y = 4
				d.interlace = 2
			case 2:
				d.step = 4
				d.repeat = 2
				y = 2
				d.interlace = 3
			case 3:
				d.step = 2
				d.repeat = 1
				y = 1
				d.interlace = 0
			default:
				return true
			}
		}
		return false
	}
	// put copies one string of pixels; completed reports that the last pixel
	// of the image was written.
	put := func(p []byte, n int) (done bool) {
		for i := 0; i < n; i++ {
			if int(p[i]) != d.transparency {
				im.pix[(g.y0+y)*im.Width+g.x0+x] = p[i]
			}
			x++
			if x >= xsize {
				if newLine() {
					return true
				}
			}
		}
		return false
	}

	for {
		if state == 1 {
			d.next = d.clear + 2
			d.codesize = d.bits + 1
			d.codemask = (1 << d.codesize) - 1
			d.bufferindex = gifBuffer
			state = 2
		}
		var p []byte
		var i int
		if d.bufferindex < gifBuffer {
			// Return the buffered tail of the previous code.
			i = gifBuffer - d.bufferindex
			p = d.buffer[d.bufferindex:gifBuffer]
			d.bufferindex = gifBuffer
		} else {
			// Get the next symbol.
			for d.bitcount < d.codesize {
				if d.blocksize > 0 {
					if ptr >= len(stream) {
						return truncatedError(len(stream) - ptr)
					}
					c := stream[ptr]
					ptr++
					d.blocksize--
					d.bitbuffer |= uint32(c) << uint(d.bitcount)
					d.bitcount += 8
				} else {
					if len(stream)-ptr < 1 {
						return truncatedError(len(stream) - ptr)
					}
					c := int(stream[ptr])
					if len(stream)-ptr < c+1 {
						return truncatedError(len(stream) - ptr)
					}
					d.blocksize = c
					ptr++
				}
			}
			c := int(d.bitbuffer) & d.codemask
			d.bitbuffer >>= uint(d.codesize)
			d.bitcount -= d.codesize

			if c == d.clear {
				if state != 2 {
					state = 1
				}
				continue
			}
			if c == d.end {
				return nil
			}

			i = 1
			if state == 2 {
				if c > d.clear {
					return decoderError(-2)
				}
				d.lastdata = byte(c)
				d.lastcode = c
				state = 3
			} else {
				thiscode := c
				if c > d.next {
					return decoderError(-2)
				}
				if c == d.next {
					if d.bufferindex <= 0 {
						return decoderError(-2)
					}
					d.bufferindex--
					d.buffer[d.bufferindex] = d.lastdata
					c = d.lastcode
				}
				for c >= d.clear {
					if d.bufferindex <= 0 || c >= gifTable {
						return decoderError(-2)
					}
					d.bufferindex--
					d.buffer[d.bufferindex] = d.data[c]
					c = int(d.link[c])
				}
				d.lastdata = byte(c)
				if d.next < gifTable {
					d.data[d.next] = byte(c)
					d.link[d.next] = uint16(d.lastcode)
					if d.next == d.codemask && d.codesize < gifBits {
						d.codesize++
						d.codemask = (1 << d.codesize) - 1
					}
					d.next++
				}
				d.lastcode = thiscode
			}
			p = []byte{d.lastdata}
		}
		if y >= ysize {
			return decoderError(-1)
		}
		if put(p, i) {
			return nil
		}
	}
}
