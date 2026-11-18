package pdfexport

import (
	"bytes"
	"crypto/md5"
	"encoding/hex"
	"errors"
	"fmt"
	"image/jpeg"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilimage"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// This file ports the image side of reportlab: lib/utils.py's ImageReader,
// pdfgen/canvas.py's drawImage, pdfbase/pdfdoc.py's PDFImageXObject and
// pdfbase/pdfutils.py's readJPEGInfo. The pixels and the Pillow exceptions come
// from internal/pilimage; this file turns them into the XObject reportlab
// writes and the failures reportlab raises while it draws.

// imageReader is reportlab's ImageReader: the bytes one source answered, the
// image Pillow opens from them, and what drawing it builds.
type imageReader struct {
	image *pilimage.Image // Image.open(data)
	data  []byte          // the bytes of the source

	// identity is self.identity() as the annotations report it.
	identity string

	// object is the XObject the first draw builds and every later draw reuses;
	// err is the failure of that build, raised again on every draw.
	object *pdfImage
	err    error
}

// newImageReader is ImageReader.__init__: Image.open of the bytes reportlab
// read, with identity as the reader names itself in annotations.
func newImageReader(data []byte, identity string) (*imageReader, error) {
	image, err := pilimage.Open(data)
	if err != nil {
		return nil, err
	}
	return &imageReader{image: image, data: data, identity: identity}, nil
}

// bytesIdentity is ImageReader.identity() of a BytesIO source: the stream has
// no name, so the annotation names only the reader. Its address is
// pystr.ObjectAddress (approved divergence X18).
const bytesIdentity = "[ImageReader@" + pystr.ObjectAddress + "]"

// inlineIdentity is ImageReader.identity() of a source name: the reader of a
// str source carries it as its fileName.
func inlineIdentity(src string) string {
	return "[ImageReader@" + pystr.ObjectAddress + " filename=" + dto.PythonStringRepr(src) + "]"
}

// pdfImage is pdfdoc.PDFImageXObject: one image XObject's dictionary and its
// filtered stream.
type pdfImage struct {
	name             string // xObjectName of the digest reportlab computes
	width, height    int
	bitsPerComponent int
	colorSpace       string   // DeviceGray, DeviceRGB or DeviceCMYK
	filters          []string // ASCII85Decode, then FlateDecode or DCTDecode
	content          []byte   // the stream content, already filtered
	mask             string   // the text of the /Mask array, "" for none
	decode           []int    // the /Decode array, nil for none
	smask            *pdfImage
}

var mode2CS = map[string]string{"RGB": "DeviceRGB", "L": "DeviceGray", "CMYK": "DeviceCMYK"}

// pdfObject is canvas.drawImage's first step for an ImageReader: getRGBData
// (which reportlab calls for every image, so a decode failure fails the
// document) and the PDFImageXObject built from the pixels or from the file.
func (r *imageReader) pdfObject() (*pdfImage, error) {
	if r.object != nil {
		return r.object, nil
	}
	if r.err != nil {
		return nil, r.err
	}
	object, err := r.buildObject()
	if err != nil {
		r.err = err
		return nil, err
	}
	r.object = object
	return object, nil
}

// buildObject is the first draw of the image: getRGBData, the name, the
// object, and for a soft mask the object registered after it.
func (r *imageReader) buildObject() (*pdfImage, error) {
	if r.image.Format == "JPEG" {
		// drawImage calls getRGBData for a JPEG too: Pillow decodes the whole
		// image before reportlab decides to embed the file as it is.
		if err := jpegDecode(r.data); err != nil {
			return nil, r.drawFailure(err)
		}
		// The name reportlab computes is md5 of Pillow's pixels, which another
		// JPEG decoder does not reproduce bit for bit: the port names it after
		// the file instead (the standard numbers image names by appearance).
		return jpegObject(formName(r.data, []byte("auto")), r.data), nil
	}
	mode, pixels, alpha, err := r.image.RGBData()
	if err != nil {
		return nil, r.drawFailure(err)
	}
	mdata := []byte("auto")
	if alpha != nil {
		mdata = alpha
	}
	object := &pdfImage{
		name: formName(pixels, mdata), width: r.image.Width, height: r.image.Height,
		bitsPerComponent: 8, colorSpace: mode2CS[mode], filters: []string{"ASCII85Decode", "FlateDecode"},
		content: ascii85Encode(deflate(pixels)),
	}
	// PDFImageXObject._checkTransparency with mask='auto': an alpha band is an
	// /SMask image object, a palette transparency a colour-key /Mask.
	if alpha != nil {
		object.smask = &pdfImage{
			name: formName(alpha), width: r.image.Width, height: r.image.Height,
			bitsPerComponent: 8, colorSpace: "DeviceGray", filters: []string{"ASCII85Decode", "FlateDecode"},
			content: ascii85Encode(deflate(alpha)), decode: []int{0, 1},
		}
	} else if transparent := r.image.Transparent(); len(transparent) > 0 {
		// getTransparent returns palette[t*3:t*3+3]; indexing it needs all
		// three bytes, as reportlab does (an empty slice is falsy there).
		if len(transparent) < 3 {
			return nil, r.drawFailure(errors.New("index out of range"))
		}
		object.mask = fmt.Sprintf("[ %d %d %d %d %d %d ]", transparent[0], transparent[0],
			transparent[1], transparent[1], transparent[2], transparent[2])
	}
	return object, nil
}

// drawFailure is the exception getRGBData raises: annotateException puts
// '\nidentity=%s' before the Pillow error's text.
func (r *imageReader) drawFailure(err error) error {
	text := err.Error()
	var pillow *pilimage.Error
	if errors.As(err, &pillow) {
		text = pillow.Text
	}
	return layoutFailure("\nidentity=" + r.identity + " " + text)
}

// imageSourceFailure is the text annotateException gives an ImageReader
// constructor's failure: '\nfileName=%r identity=%s' and the exception's text.
func imageSourceFailure(src, text string) string {
	name := dto.PythonStringRepr(src)
	return "\nfileName=" + name + " identity=[ImageReader@" + pystr.ObjectAddress + " filename=" + name + "] " + text
}

// pillowText is str() of an error pilimage reports.
func pillowText(err error) string {
	var pillow *pilimage.Error
	if errors.As(err, &pillow) {
		return pillow.Text
	}
	return err.Error()
}

// formName is xObjectName(_digester(parts)): "FormXob." and the md5 of the
// concatenated parts.
func formName(parts ...[]byte) string {
	digest := md5.New()
	for _, part := range parts {
		_, _ = digest.Write(part)
	}
	return "FormXob." + hex.EncodeToString(digest.Sum(nil))
}

// cannedStream is PDFImageXObject.__init__'s streamContent: a 24x23 one-bit
// grey image reportlab keeps when it cannot read a JPEG's dimensions.
const cannedStream = "\n            003B00 002700 002480 0E4940 114920 14B220 3CB650\n" +
	"            75FE88 17FF8C 175F14 1C07E2 3803C4 703182 F8EDFC\n" +
	"            B2BBC2 BB6F84 31BFC2 18EA3C 0E3E00 07FC00 03F800\n" +
	"            1E1800 1FF800>\n            "

// jpegObject is PDFImageXObject.loadImageFromJPEG: the file embedded as it is
// when readJPEGInfo reads its dimensions, else the canned image the
// constructor set, whose /Mask reportlab still writes as PDFArray('auto').
func jpegObject(name string, data []byte) *pdfImage {
	width, height, components, ok := readJPEGInfo(data)
	if !ok {
		return &pdfImage{name: name, width: 24, height: 23, bitsPerComponent: 1,
			colorSpace: "DeviceGray", filters: []string{"ASCII85Decode"}, content: []byte(cannedStream),
			mask: "[ a u t o ]"}
	}
	image := &pdfImage{name: name, width: width, height: height, bitsPerComponent: 8,
		colorSpace: "DeviceGray", filters: []string{"ASCII85Decode", "DCTDecode"}, content: ascii85Encode(data)}
	switch components {
	case 1:
		image.colorSpace = "DeviceGray"
	case 3:
		image.colorSpace = "DeviceRGB"
	default:
		image.colorSpace = "DeviceCMYK"
		image.decode = []int{1, 0, 1, 0, 1, 0, 1, 0}
	}
	return image
}

// jpegDecode is the decode drawImage's getRGBData performs on a JPEG, with the
// approved divergence X19 (image codec libraries, platform): an image Go's
// decoder reads is drawn; a feature it does not implement (arithmetic coding,
// 12-bit precision and the like) is read by libjpeg-turbo and is drawn too; a
// decode error is Pillow's truncated-file error when the entropy-coded data
// ends before an EOI marker, and otherwise libjpeg-turbo continues past the
// corrupt data with a warning.
//
// The number in the truncated-file message is libjpeg-turbo's read-ahead and
// Go's decoder does not report it: the port counts the entropy-coded bytes the
// stream still holds, which is the number Pillow gives for the small images
// whose single MCU row libjpeg-turbo has not consumed.
func jpegDecode(data []byte) error {
	_, err := jpeg.Decode(bytes.NewReader(data))
	if err == nil {
		return nil
	}
	var unsupported jpeg.UnsupportedError
	if errors.As(err, &unsupported) {
		return nil
	}
	start, ok := jpegEntropyStart(data)
	if ok && !jpegHasEOI(data, start) {
		return &pilimage.Error{Class: "OSError",
			Text: fmt.Sprintf("image file is truncated (%d bytes not processed)", len(data)-start)}
	}
	return nil
}

// jpegEntropyStart is the offset of the entropy-coded data after the first SOS
// marker, or false when the marker segments end before a complete SOS.
func jpegEntropyStart(data []byte) (int, bool) {
	if len(data) < 2 || data[0] != 0xFF || data[1] != 0xD8 {
		return 0, false
	}
	for pos := 2; pos+1 < len(data); {
		if data[pos] != 0xFF {
			pos++
			continue
		}
		marker := data[pos+1]
		switch {
		case marker == 0xFF:
			pos++
		case marker == 0xD8 || marker == 0x01 || marker >= 0xD0 && marker <= 0xD7:
			pos += 2
		case marker == 0xD9:
			return 0, false
		default:
			if pos+3 >= len(data) {
				return 0, false
			}
			length := int(data[pos+2])<<8 | int(data[pos+3])
			if length < 2 || pos+2+length > len(data) {
				return 0, false
			}
			if marker == 0xDA {
				return pos + 2 + length, true
			}
			pos += 2 + length
		}
	}
	return 0, false
}

// jpegHasEOI reports whether the scan data after start reaches an EOI marker.
func jpegHasEOI(data []byte, start int) bool {
	for pos := start; pos+1 < len(data); {
		if data[pos] != 0xFF {
			pos++
			continue
		}
		marker := data[pos+1]
		switch {
		case marker == 0x00 || marker >= 0xD0 && marker <= 0xD7:
			pos += 2
		case marker == 0xD9:
			return true
		case marker >= 0xC0:
			if pos+3 >= len(data) {
				return false
			}
			length := int(data[pos+2])<<8 | int(data[pos+3])
			if length < 2 {
				return false
			}
			pos += 2 + length
		default:
			pos += 2
		}
	}
	return false
}

// readJPEGInfo is pdfutils.readJPEGInfo: the width, height and component count
// of a JPEG, and false where reportlab's reader raises (an unsupported or
// malformed segment, a precision other than 8, a file that ends first).
func readJPEGInfo(data []byte) (width, height, components int, ok bool) {
	pos := 0
	read := func(n int) ([]byte, bool) {
		if n < 0 {
			n = 0
		}
		if pos+n > len(data) {
			return nil, false
		}
		out := data[pos : pos+n]
		pos += n
		return out, true
	}
	take := func(n int) []byte {
		// BytesIO.read(n) with a negative n reads the rest of the stream.
		if n < 0 || n > len(data)-pos {
			n = len(data) - pos
		}
		out := data[pos : pos+n]
		pos += n
		return out
	}
	for {
		first, valid := read(1)
		if !valid {
			return 0, 0, 0, false
		}
		if first[0] != 0xFF {
			continue
		}
		second, valid := read(1)
		if !valid {
			return 0, 0, 0, false
		}
		switch marker := second[0]; {
		case marker == 0xC0 || marker == 0xC1 || marker == 0xC2:
			pos += 2 // skip the segment length
			precision, valid := read(1)
			if !valid {
				return 0, 0, 0, false
			}
			if precision[0] != 8 {
				return 0, 0, 0, false // PDFError('JPEG must have 8 bits per component')
			}
			ys, valid := read(2)
			if !valid {
				return 0, 0, 0, false
			}
			height = int(ys[0])<<8 | int(ys[1])
			xs, valid := read(2)
			if !valid {
				return 0, 0, 0, false
			}
			width = int(xs[0])<<8 | int(xs[1])
			colors, valid := read(1)
			if !valid {
				return 0, 0, 0, false
			}
			return width, height, int(colors[0]), true
		case marker == 0xE0:
			segment, valid := read(2)
			if !valid {
				return 0, 0, 0, false
			}
			// reportlab reads the segment and takes x[10:12] and x[8:10] as
			// the density: a payload shorter than 12 bytes raises.
			block := take((int(segment[0])<<8 | int(segment[1])) - 2)
			if len(block) < 12 {
				return 0, 0, 0, false
			}
		case unsupportedJPEGMarker(marker):
			return 0, 0, 0, false
		case noParamJPEGMarker(marker):
			// A marker without parameters: the next byte continues the scan.
		default:
			segment, valid := read(2)
			if !valid {
				return 0, 0, 0, false
			}
			length := (int(segment[0])<<8 | int(segment[1])) - 2
			pos += length
			if pos < 0 || pos > len(data) {
				return 0, 0, 0, false
			}
		}
	}
}

// unsupportedJPEGMarker is readJPEGInfo's unsupportedMarkers.
func unsupportedJPEGMarker(marker byte) bool {
	switch marker {
	case 0xC3, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF:
		return true
	}
	return false
}

// noParamJPEGMarker is readJPEGInfo's noParamMarkers.
func noParamJPEGMarker(marker byte) bool {
	switch marker {
	case 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0x01:
		return true
	}
	return false
}
