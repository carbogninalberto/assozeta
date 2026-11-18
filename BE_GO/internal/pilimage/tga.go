package pilimage

import "encoding/binary"

// This file ports TgaImagePlugin and its "tga_rle" decoder.

var tgaModes = map[[2]int]string{
	{1, 8}:  "P",
	{3, 1}:  "1",
	{3, 8}:  "L",
	{3, 16}: "LA",
	{2, 16}: "BGRA;15Z",
	{2, 24}: "BGR",
	{2, 32}: "BGRA",
}

// tgaLoad decodes the first frame: raw or RLE, palette realised, then the
// footer-driven alpha fill and horizontal flip TgaImageFile.load_end applies.
type tgaLoad struct {
	offset      int
	rawmode     string
	orientation int
	depth       int
	rle         bool
	flip        bool
	palette     []byte
	palRawmode  string
}

func (im *Image) loadTGA(t *tgaLoad) error {
	if t.rle {
		if err := im.tgaRLE(t); err != nil {
			return err
		}
	} else {
		if err := im.rawTile(im.data, t.offset, 0, t.orientation, t.rawmode); err != nil {
			return err
		}
	}
	if t.palette != nil {
		// palette realised by openTGA
	}
	if im.Mode == "RGBA" {
		// TgaImageFile.load_end: a version-2 footer with no alpha attribute
		// makes the alpha band opaque.
		if len(im.data) >= 26 {
			footer := im.data[len(im.data)-26:]
			if len(footer) == 26 && string(footer[8:26]) == "TRUEVISION-XFILE.\x00" {
				ext := int(int32(binary.LittleEndian.Uint32(footer)))
				if ext != 0 {
					pos := ext + 494
					if pos < len(im.data) && im.data[pos] == 0 {
						for i := 3; i < len(im.pix); i += 4 {
							im.pix[i] = 255
						}
					}
				}
			}
		}
	}
	if t.flip {
		im.flipHorizontal()
	}
	return nil
}

// tgaRLE is ImagingTgaRleDecode.
func (im *Image) tgaRLE(t *tgaLoad) error {
	data := im.data
	rowBytes := rawRowBytes(t.rawmode, im.Width)
	depth := t.depth
	im.ensurePix()
	buffer := make([]byte, rowBytes)
	ptr := t.offset
	x := 0
	row := 0
	ystep := t.orientation
	y := row
	if ystep < 0 {
		y = im.Height - 1
	}
	for {
		if ptr >= len(data) {
			return truncatedError(0)
		}
		b := data[ptr]
		n := depth * (int(b&0x7f) + 1)
		extra := 0
		if b&0x80 != 0 {
			if ptr+1+depth > len(data) {
				return truncatedError(len(data) - ptr)
			}
			if x+n > rowBytes {
				return decoderError(-1)
			}
			for i := 0; i < n; i += depth {
				copy(buffer[x+i:], data[ptr+1:ptr+1+depth])
			}
			ptr += 1 + depth
		} else {
			if ptr+1+n > len(data) {
				return truncatedError(len(data) - ptr)
			}
			if x+n > rowBytes {
				extra = n
				n = rowBytes - x
				extra -= n
			}
			copy(buffer[x:], data[ptr+1:ptr+1+n])
			ptr += 1 + n
		}
		for {
			x += n
			if x >= rowBytes {
				if err := im.rawRow(buffer, 0, y, im.Width, t.rawmode, baseRawmode(t.rawmode), im.samplesPerPixel(), -1); err != nil {
					return err
				}
				x = 0
				y += ystep
				if y < 0 || y >= im.Height {
					return nil
				}
			}
			if extra == 0 {
				break
			}
			if x > 0 {
				break
			}
			n = extra
			if n > rowBytes {
				n = rowBytes
			}
			if ptr+n > len(data) {
				return truncatedError(0)
			}
			copy(buffer, data[ptr:ptr+n])
			ptr += n
			extra -= n
		}
	}
}

// flipHorizontal is Image.transpose(FLIP_LEFT_RIGHT).
func (im *Image) flipHorizontal() {
	n := im.samplesPerPixel()
	for y := 0; y < im.Height; y++ {
		for x := 0; x < im.Width/2; x++ {
			a := (y*im.Width + x) * n
			b := (y*im.Width + im.Width - 1 - x) * n
			for s := 0; s < n; s++ {
				im.pix[a+s], im.pix[b+s] = im.pix[b+s], im.pix[a+s]
			}
		}
	}
}
