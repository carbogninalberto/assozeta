// Overlay of the images fill_pdf_fields draws on the first page of the
// Piemonte Biella form before pypdf fills it: PyMuPDF opens the signature (and
// stamp) file, inserts it into the page's content as an image XObject and saves
// the document, which this package reproduces as an incremental update of the
// filled form. The page is drawn the way PyMuPDF 1.25.5's Page.insert_image
// draws it, so the result is visually equal to Django's.
package medicalform

import (
	"errors"
	"strings"
)

// StampKey is the field fill_pdf_fields draws under the signature.
const StampKey = "sport_association.stamp"

// Rect is a rectangle in PyMuPDF page coordinates: x to the right, y down
// from the top of the page as displayed.
type Rect struct {
	X0, Y0, X1, Y1 float64
}

// Image is an image file MuPDF can open, placed at a page rectangle.
type Image struct {
	file []byte
	rect Rect
}

// NewImage reports whether MuPDF 1.25.5 opens the file as an image and
// decodes it. The formats decoded here are PNG, JPEG, GIF, BMP, TIFF, PNM
// (P1-P6, PAM P7 and PFM) and PSD; another format is refused (fill_pdf_fields'
// block then draws no image at all).
func NewImage(file []byte) (Image, bool) {
	if _, ok := decodeImage(file); !ok {
		return Image{}, false
	}
	return Image{file: append([]byte(nil), file...)}, true
}

// At places the image in a rectangle, as Page.insert_image(rect) does.
func (i Image) At(x0, y0, x1, y1 float64) Image {
	i.rect = Rect{X0: x0, Y0: y0, X1: x1, Y1: y1}
	return i
}

// FillImages is the list of images fill_pdf_fields draws for the form values:
// the president signature at (400, 20, 520, 1300) and, in its own try block,
// the stamp at (100, 20, 260, 1300). A missing signature key, a value whose
// base64 does not decode or a file MuPDF cannot open skips the whole overlay;
// a stamp that fails is skipped alone.
func FillImages(values map[string]string) []Image {
	value, present := values[SignatureKey]
	if !present {
		return nil
	}
	file, ok := imageFile(value)
	if !ok {
		return nil
	}
	signature, ok := NewImage(file)
	if !ok {
		return nil
	}
	images := []Image{signature.At(400, 20, 520, 1300)}
	value, present = values[StampKey]
	if !present {
		return images
	}
	file, ok = imageFile(value)
	if !ok {
		return images
	}
	stamp, ok := NewImage(file)
	if !ok {
		return images
	}
	return append(images, stamp.At(100, 20, 260, 1300))
}

// imageFile is fill_pdf_fields' file contents for one value: the text after
// the first ';base64,' when the value has one, binascii.a2b_base64 in its
// default (non-strict) mode.
func imageFile(value string) ([]byte, bool) {
	if parts := strings.Split(value, ";base64,"); len(parts) > 1 {
		value = parts[1]
	}
	file, err := a2bBase64([]byte(value))
	if err != nil || len(file) == 0 {
		return nil, false
	}
	return file, true
}

const b64Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

// a2bBase64 is binascii.a2b_base64 in its default (non-strict) mode as Python
// 3.13.16 behaves: characters outside the alphabet and '=' are skipped while
// decoding; at the end an incomplete quad is accepted only when the '=' after
// the last data character complete it. base64.b64decode encodes a str as
// ASCII first, so a byte outside ASCII is an error.
func a2bBase64(data []byte) ([]byte, error) {
	var out []byte
	quad, pads := 0, 0
	var left byte
	for _, c := range data {
		if c >= 0x80 {
			return nil, errors.New("string argument should contain only ASCII characters")
		}
		if c == '=' {
			pads++
			continue
		}
		index := strings.IndexByte(b64Alphabet, c)
		if index < 0 {
			continue
		}
		value := byte(index)
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, value
		case 1:
			quad = 2
			out = append(out, left<<2|value>>4)
			left = value & 0x0f
		case 2:
			quad = 3
			out = append(out, left<<4|value>>2)
			left = value & 0x03
		default:
			quad = 0
			out = append(out, left<<6|value)
			left = 0
		}
	}
	if quad == 1 || quad != 0 && quad+pads < 4 {
		return nil, errors.New("binascii.Error")
	}
	return out, nil
}

// FillBiellaWithImages is FillBiella followed by PyMuPDF's insert_image of
// each image on the first page (overlay=True: after the page's own content).
func FillBiellaWithImages(values map[string]string, images []Image) []byte {
	filled := FillBiella(values)
	if len(images) == 0 {
		return filled
	}
	overlaid, err := overlayImages(filled, images)
	if err != nil {
		// The filled form is this package's own output: a structure it cannot
		// read means the values are not one of the form's keys.
		return filled
	}
	return overlaid
}

// ─── image files ────────────────────────────────────────────────────────────

type imageAsset struct {
	width, height int
	gray          bool
	color         []byte // 8-bit samples, rows top to bottom, no alpha
	alpha         bool   // the file has a transparency channel
	mask          []byte // alpha samples, packed when maskBits is 1
	maskBits      int
	jpeg          []byte // DCTDecode stream when the file is a JPEG
	cmyk          bool
}

// fzMul255 and fzDiv255 are MuPDF's premultiply and un-premultiply of an
// 8-bit sample (include/mupdf/fitz/geometry.h).
func fzMul255(a, b int) int {
	x := a*b + 128
	return (x + x>>8) >> 8
}

func fzDiv255(c, a int) int {
	if a == 0 {
		return 0
	}
	return c * (255 * 256 / a) >> 8
}

// packBits packs one bit per sample (255 is 1, anything else 0), each row
// padded to a byte boundary, most significant bit first.
func packBits(alpha []byte, width, height int) []byte {
	row := (width + 7) / 8
	packed := make([]byte, row*height)
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			if alpha[y*width+x] == 0xff {
				packed[y*row+x/8] |= 1 << (7 - uint(x%8))
			}
		}
	}
	return packed
}
