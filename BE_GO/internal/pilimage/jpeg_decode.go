package pilimage

import (
	"bytes"
	"encoding/binary"
	"errors"
	"image"
	"image/jpeg"
)

// This file decodes JPEG streams the way Pillow's JPEG plugin does for the
// formats this package feeds it (JPEG files, JPEG-in-IPTC, BLP1 JPEG and
// JPEG-in-TIFF). Pixels come from Go's image/jpeg decoder: approved
// divergence X19 (image codec libraries, platform) covers the rounding
// differences against libjpeg-turbo, so callers compare only mode, size and
// Latin.

// jpegUnsupported reports whether err is a feature Go's decoder does not
// implement (arithmetic coding, 12-bit samples, ...); libjpeg-turbo decodes
// those (X19).
func jpegUnsupported(err error) bool {
	var unsupported jpeg.UnsupportedError
	return errors.As(err, &unsupported)
}

// jpegEntropyStart is the offset of the entropy-coded data after the first
// SOS marker, or false when the marker segments end before a complete SOS.
func jpegEntropyStart(data []byte) (int, bool) {
	if len(data) < 2 || data[0] != 0xFF || data[1] != 0xD8 {
		return 0, false
	}
	pos := 2
	for pos+4 <= len(data) {
		if data[pos] != 0xFF {
			return 0, false
		}
		for pos < len(data) && data[pos] == 0xFF {
			pos++
		}
		if pos >= len(data) {
			return 0, false
		}
		marker := data[pos]
		pos++
		if marker == 0xD8 || marker == 0xD9 || (marker >= 0xD0 && marker <= 0xD7) {
			continue
		}
		if pos+2 > len(data) {
			return 0, false
		}
		n := int(binary.BigEndian.Uint16(data[pos:]))
		if n < 2 || pos+n > len(data) {
			return 0, false
		}
		pos += n
		if marker == 0xDA {
			return pos, true
		}
	}
	return 0, false
}

// jpegHasEOI reports whether an EOI marker follows off outside entropy data
// 0xFF00 stuffing.
func jpegHasEOI(data []byte, off int) bool {
	for i := off; i+1 < len(data); i++ {
		if data[i] == 0xFF && data[i+1] == 0xD9 {
			return true
		}
	}
	return false
}

// jpegDecodeError is the exception Pillow's JpegImageFile.load raises when
// Go's decoder refuses the stream. A stream that ends before an EOI marker is
// Pillow's truncated-file error; everything else is the decoder status text.
// The byte count in the truncated message is the pending read buffer libjpeg
// reports, which Go's decoder does not expose: the port counts the
// entropy-coded bytes the stream still holds, which is the number Pillow
// gives for images whose MCU row fits the first read (X19).
func jpegDecodeError(data []byte, err error) *Error {
	if jpegUnsupported(err) {
		return osError("broken data stream when reading image file")
	}
	if start, ok := jpegEntropyStart(data); ok && !jpegHasEOI(data, start) {
		return osError("image file is truncated (" + itoa(len(data)-start) + " bytes not processed)")
	}
	return osError("broken data stream when reading image file")
}

// decodeJPEGImage is JpegImagePlugin.load for a complete stream: the pixel
// samples in the mode Pillow reports. Go's decoder supplies the pixels (X19).
func decodeJPEGImage(data []byte) (*Image, error) {
	src, err := jpeg.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, jpegDecodeError(data, err)
	}
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	out := newImage(data, "JPEG", "RGB", w, h)
	switch src := src.(type) {
	case *image.Gray:
		out.Mode = "L"
		out.pix = make([]byte, w*h)
		for y := 0; y < h; y++ {
			row := src.Pix[(b.Min.Y+y-src.Rect.Min.Y)*src.Stride+(b.Min.X-src.Rect.Min.X):]
			copy(out.pix[y*w:(y+1)*w], row[:w])
		}
	case *image.CMYK:
		out.Mode = "CMYK"
		out.pix = make([]byte, w*h*4)
		for y := 0; y < h; y++ {
			row := src.Pix[(b.Min.Y+y-src.Rect.Min.Y)*src.Stride+(b.Min.X-src.Rect.Min.X)*4:]
			copy(out.pix[y*w*4:(y+1)*w*4], row[:w*4])
		}
	default:
		out.pix = make([]byte, w*h*3)
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				r, g, b, _ := src.At(b.Min.X+x, b.Min.Y+y).RGBA()
				p := (y*w + x) * 3
				out.pix[p], out.pix[p+1], out.pix[p+2] = byte(r>>8), byte(g>>8), byte(b>>8)
			}
		}
	}
	return out, nil
}

// jpegRGB decodes a JPEG stream and returns its pixels converted to RGB, the
// bytes BLP1's set_as_raw and the TIFF JPEG codec consume.
func jpegRGB(data []byte) (w, h int, pix []byte, err error) {
	dec, err := decodeJPEGImage(data)
	if err != nil {
		return 0, 0, nil, err
	}
	if dec.Mode == "RGB" {
		return dec.Width, dec.Height, dec.pix, nil
	}
	rgb, err := dec.convertRGB()
	if err != nil {
		return 0, 0, nil, err
	}
	return dec.Width, dec.Height, rgb, nil
}

// loadJPEG is JpegImagePlugin.load.
func (im *Image) loadJPEG() error {
	dec, err := decodeJPEGImage(im.data)
	if err != nil {
		return err
	}
	im.Mode = dec.Mode
	im.Width, im.Height = dec.Width, dec.Height
	im.pix = dec.pix
	return nil
}
