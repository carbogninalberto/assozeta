// Overlay of the images fill_pdf_fields draws on the first page of the
// Piemonte Biella form before pypdf fills it: PyMuPDF opens the signature (and
// stamp) file, inserts it into the page's content as an image XObject and saves
// the document, which this package reproduces as an incremental update of the
// filled form. The page is drawn the way PyMuPDF 1.25.5's Page.insert_image
// draws it, so the result is visually equal to Django's.
package medicalform

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/gif"
	"image/jpeg"
	"image/png"
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
// decodes it. The formats decoded here are PNG, JPEG and GIF; another format
// is refused (fill_pdf_fields' block then draws no image at all).
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

var errImage = errors.New("medicalform: unknown image file format")

func decodeImage(file []byte) (imageAsset, bool) {
	switch {
	case bytes.HasPrefix(file, []byte("\x89PNG\r\n\x1a\n")):
		return decodePNG(file)
	case bytes.HasPrefix(file, []byte("\xff\xd8")):
		return decodeJPEG(file)
	case bytes.HasPrefix(file, []byte("GIF87a")), bytes.HasPrefix(file, []byte("GIF89a")):
		return decodeGIF(file)
	}
	return imageAsset{}, false
}

func decodePNG(file []byte) (imageAsset, bool) {
	decoded, err := png.Decode(bytes.NewReader(file))
	if err != nil {
		return imageAsset{}, false
	}
	// IHDR: signature (8), length (4), type (4), width (4), height (4), depth (1), colour type (1).
	if len(file) < 26 {
		return imageAsset{}, false
	}
	colourType := file[25]
	gray := colourType == 0 || colourType == 4
	// The png loader gives the image an alpha channel for colour types 4 and
	// 6 and for a tRNS chunk, even when every sample is opaque.
	alpha := colourType == 4 || colourType == 6 || pngHasTRNS(file)
	return newAsset(decoded, gray, alpha), true
}

// pngHasTRNS reports whether the PNG has a tRNS chunk before its image data.
func pngHasTRNS(file []byte) bool {
	position := 8
	for position+8 <= len(file) {
		length := int(file[position])<<24 | int(file[position+1])<<16 | int(file[position+2])<<8 | int(file[position+3])
		kind := string(file[position+4 : position+8])
		switch kind {
		case "tRNS":
			return true
		case "IDAT", "IEND":
			return false
		}
		if length < 0 || position+12+length > len(file) {
			return false
		}
		position += 12 + length
	}
	return false
}

func decodeGIF(file []byte) (imageAsset, bool) {
	decoded, err := gif.Decode(bytes.NewReader(file))
	if err != nil {
		return imageAsset{}, false
	}
	// The gif loader always yields an alpha channel, opaque or not.
	return newAsset(decoded, false, true), true
}

func decodeJPEG(file []byte) (imageAsset, bool) {
	config, err := jpeg.DecodeConfig(bytes.NewReader(file))
	if err != nil || config.Width <= 0 || config.Height <= 0 {
		return imageAsset{}, false
	}
	if !jpegHasHeaderTables(file) {
		// MuPDF reads the frame and the tables: a JPEG cut before its scan
		// header is refused ("missing SOS marker"), a later cut is not.
		return imageAsset{}, false
	}
	asset := imageAsset{width: config.Width, height: config.Height, jpeg: file}
	switch config.ColorModel {
	case color.GrayModel:
		asset.gray = true
	case color.CMYKModel:
		asset.cmyk = true
	}
	return asset, true
}

// jpegHasHeaderTables reports whether the marker segments of a JPEG reach its
// start-of-scan marker: MuPDF's image opener parses the tables up to there.
func jpegHasHeaderTables(file []byte) bool {
	position := 2 // past SOI
	for position+1 < len(file) {
		if file[position] != 0xff {
			return false
		}
		marker := file[position+1]
		position += 2
		switch {
		case marker == 0xff: // fill byte
			position--
			continue
		case marker == 0x01, marker >= 0xd0 && marker <= 0xd8: // standalone markers
			continue
		case marker == 0xd9: // end of image: no scan
			return false
		}
		if position+2 > len(file) {
			return false
		}
		length := int(file[position])<<8 | int(file[position+1])
		if length < 2 || position+length > len(file) {
			return false
		}
		if marker == 0xda { // start of scan
			return true
		}
		position += length
	}
	return false
}

// newAsset converts a decoded image to MuPDF's image samples: DeviceGray or
// DeviceRGB, 8 bits per component (a 16-bit sample keeps its high byte), with
// the image's alpha channel as a separate mask, as pdf_add_image writes it.
// The colour samples follow MuPDF's premultiply-then-divide round trip
// (fz_mul255, fz_div255).
func newAsset(decoded image.Image, gray, alpha bool) imageAsset {
	bounds := decoded.Bounds()
	width, height := bounds.Dx(), bounds.Dy()
	channels := 3
	if gray {
		channels = 1
	}
	asset := imageAsset{width: width, height: height, gray: gray}
	asset.color = make([]byte, 0, width*height*channels)
	alphaSamples := make([]byte, 0, width*height)
	binaryAlpha := true
	for y := bounds.Min.Y; y < bounds.Max.Y; y++ {
		for x := bounds.Min.X; x < bounds.Max.X; x++ {
			// NRGBA64 is straight (non-premultiplied) alpha in 16 bits: the
			// high byte is the 8-bit sample MuPDF keeps.
			pixel := color.NRGBA64Model.Convert(decoded.At(x, y)).(color.NRGBA64)
			r, g, b, a := byte(pixel.R>>8), byte(pixel.G>>8), byte(pixel.B>>8), byte(pixel.A>>8)
			if gray {
				asset.color = append(asset.color, r)
			} else {
				asset.color = append(asset.color, r, g, b)
			}
			alphaSamples = append(alphaSamples, a)
			if a != 0 && a != 0xff {
				binaryAlpha = false
			}
		}
	}
	if !alpha {
		return asset
	}
	// The mask holds the alpha channel; the colour samples are the
	// premultiplied ones divided by alpha again, as pdf_add_image writes them.
	for i := 0; i < len(alphaSamples); i++ {
		a := int(alphaSamples[i])
		for c := 0; c < channels; c++ {
			at := i*channels + c
			asset.color[at] = byte(fzDiv255(fzMul255(int(asset.color[at]), a), a))
		}
	}
	asset.alpha = true
	if !binaryAlpha {
		asset.mask, asset.maskBits = alphaSamples, 8
		return asset
	}
	asset.mask, asset.maskBits = packBits(alphaSamples, width, height), 1
	return asset
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
