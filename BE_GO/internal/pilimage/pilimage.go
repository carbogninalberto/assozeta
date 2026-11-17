// Package pilimage reproduces, for image bytes, what Pillow 13.0.0's
// Image.open(BytesIO(data)) and reportlab 4.2.5's ImageReader return.
//
// Open identifies the data the way Image.open does (including the order in
// which the format plugins are tried) and RGBData returns the pixels
// ImageReader.getRGBData would produce: the decoded image converted to "L",
// "RGB" or "CMYK", the conversion Pillow's C code performs, plus the alpha
// bytes reportlab extracts for LA and RGBA images or for palette PNGs with a
// tRNS chunk. Both are needed because reportlab embeds a non-JPEG image as
// exactly those bytes (zlib-compressed) in a PDF.
//
// The package decodes PNG, GIF, BMP/DIB and the PPM family (PBM, PGM, PPM,
// PAM-style extensions and Pf) and parses JPEG headers; data Pillow identifies
// as any other format makes Open return ErrFormatNotPorted. It depends only on
// the Go standard library.
package pilimage

import (
	"errors"
	"fmt"
	"math/big"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// Image is an image Pillow opened: Image.open(BytesIO(data)) (lazy: header
// only).
type Image struct {
	Format        string // im.format: "PNG", "GIF", "BMP", "DIB", "JPEG", "PPM", ...
	Mode          string // im.mode right after open ("1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "I", "I;16", "I;16B", "F", ...)
	Width, Height int

	data []byte // the image bytes, as given to Open

	// Decoded state, filled by load.
	pix   []byte    // samples: one byte per sample for 1/L/P/LA/RGB/RGBA/CMYK
	pix16 []uint16  // samples for mode I;16
	pixI  []int32   // samples for mode I
	pixF  []float32 // samples for mode F

	// palette is the palette as Pillow stores it (ImagePalette.palette):
	// PLTE data for PNG, the colour table for GIF, BGR(X) entries for BMP.
	palette []byte
	// palRGB is the realized palette: 256 RGB triples, zero filled.
	palRGB [768]byte
	// palAlpha is the realized palette alpha: 255 where unset.
	palAlpha [256]byte
	hasPal   bool
	// palInvalid records a palette longer than ImagingPalette allows; loading
	// such an image raises ValueError("invalid palette size").
	palInvalid bool

	// transparency is info["transparency"].
	transparency transparency

	// Per-format header state.
	png *pngState
	gif *gifState
	bmp *bmpState
	ppm *ppmState
}

type transparency struct {
	present bool
	isInt   bool
	index   int    // when isInt
	data    []byte // when !isInt (PNG tRNS byte string)
	tuple   [3]int // PNG truecolour tRNS
	isTuple bool
}

// Error is an exception Pillow raises: Class is the Python class name, Text is
// str(exception).
type Error struct{ Class, Text string }

func (e *Error) Error() string { return e.Class + ": " + e.Text }

// ErrFormatNotPorted is returned for data Pillow identifies as a format this
// package does not decode (the caller decides what to do); it is never
// returned for data Pillow cannot identify.
var ErrFormatNotPorted = errors.New("pilimage: image format not ported")

// maxImagePixels is Image.MAX_IMAGE_PIXELS.
const maxImagePixels = 1024 * 1024 * 1024 / 4 / 3

// unidentified is the UnidentifiedImageError Image.open raises for fp.
func unidentified() *Error {
	return &Error{
		Class: "UnidentifiedImageError",
		Text:  "cannot identify image file <_io.BytesIO object at " + pystr.ObjectAddress + ">",
	}
}

// skipClass reports whether ImageFile.ImageFile.__init__/Image._open_core turn
// an exception of this class into SyntaxError and try the next plugin.
func skipClass(class string) bool {
	switch class {
	case "SyntaxError", "IndexError", "TypeError", "KeyError", "EOFError", "error": // error is struct.error
		return true
	}
	return false
}

func syntaxError(text string) *Error { return &Error{Class: "SyntaxError", Text: text} }
func valueError(text string) *Error  { return &Error{Class: "ValueError", Text: text} }
func osError(text string) *Error     { return &Error{Class: "OSError", Text: text} }

// plugin is one entry of Pillow's Image.OPEN registry, in the order
// Image.open tries them for a BytesIO: BMP, DIB, GIF, JPEG, PPM, PNG (loaded
// by preinit), then the rest in the order PIL._plugins imports them.
type plugin struct {
	format string
	accept func(prefix []byte) bool
	// open parses the header. A returned *Error whose class is skipped makes
	// Open try the next plugin. Formats this package does not decode return a
	// marker Image (Format and, where the plugin sets them, mode and size) and
	// Open reports ErrFormatNotPorted.
	open func(data []byte) (*Image, error)
	// decoded marks the formats whose pixels load and convert.
	decoded bool
	// allowZero is for Pillow's stub plugins, whose success check accepts a
	// zero size.
	allowZero bool
}

var plugins = []plugin{
	{"BMP", acceptBMP, openBMP, true, false},
	{"DIB", acceptDIB, openDIB, true, false},
	{"GIF", acceptGIF, openGIF, true, false},
	{"JPEG", acceptJPEG, openJPEG, true, false},
	{"PPM", acceptPPM, openPPM, true, false},
	{"PNG", acceptPNG, openPNG, true, false},
	{"AVIF", acceptAVIF, openAVIF, false, false},
	{"BLP", acceptBLP, openBLP, false, false},
	{"BUFR", acceptBUFR, openStub("BUFR"), false, true},
	{"CUR", acceptCUR, openCUR, false, false},
	{"PCX", acceptPCX, openPCX, false, false},
	{"DCX", acceptDCX, openDCX, false, false},
	{"DDS", acceptDDS, openDDS, false, false},
	{"EPS", acceptEPS, openEPS, false, false},
	{"FITS", acceptFITS, openFITS, false, false},
	{"FLI", acceptFLI, openFLI, false, false},
	{"FTEX", acceptFTEX, openFTEX, false, false},
	{"GBR", acceptGBR, openGBR, false, false},
	{"GRIB", acceptGRIB, openStub("GRIB"), false, true},
	{"HDF5", acceptHDF5, openStub("HDF5"), false, true},
	{"JPEG2000", acceptJPEG2000, openJPEG2000, false, false},
	{"ICNS", acceptICNS, openICNS, false, false},
	{"ICO", acceptICO, openICO, false, false},
	{"IM", nil, openIM, false, false},
	{"IMT", nil, openIMT, false, false},
	{"IPTC", nil, openIPTC, false, false},
	{"MCIDAS", acceptMCIDAS, openMCIDAS, false, false},
	{"MPEG", acceptMPEG, openMPEG, false, false},
	{"TIFF", acceptTIFF, openTIFF, false, false},
	{"MSP", acceptMSP, openMSP, false, false},
	{"PCD", nil, openPCD, false, false},
	{"PIXAR", acceptPIXAR, openPIXAR, false, false},
	{"PSD", acceptPSD, openPSD, false, false},
	{"QOI", acceptQOI, openQOI, false, false},
	{"SGI", acceptSGI, openSGI, false, false},
	{"SPIDER", nil, openSPIDER, false, false},
	{"SUN", acceptSUN, openSUN, false, false},
	{"TGA", nil, openTGA, false, false},
	{"WEBP", acceptWEBP, openWEBP, false, false},
	{"WMF", acceptWMF, openWMF, false, false},
	{"XBM", acceptXBM, openXBM, false, false},
	{"XPM", acceptXPM, openXPM, false, false},
	{"XVTHUMB", acceptXVTHUMB, openXVTHUMB, false, false},
}

// openStub is the _open of Pillow's stub plugins: it only checks the signature
// and reports mode F with the default zero size.
func openStub(format string) func([]byte) (*Image, error) {
	return func([]byte) (*Image, error) {
		return &Image{Format: format, Mode: "F"}, nil
	}
}

// identified is the answer for a format this package does not decode: the
// marker's size and mode only participate in Pillow's "not identified by this
// driver" check.
func identified(format string) (*Image, error) {
	return &Image{Format: format, Mode: "L", Width: 1, Height: 1}, nil
}

// Open is Image.open(BytesIO(data)): the format Pillow identifies, its mode
// and size, or the *Error Pillow raises.
func Open(data []byte) (*Image, error) {
	prefix := data
	if len(prefix) > 16 {
		prefix = prefix[:16]
	}
	for _, p := range plugins {
		if p.accept != nil && !p.accept(prefix) {
			continue
		}
		im, err := p.open(data)
		if err != nil {
			var e *Error
			if errors.As(err, &e) && skipClass(e.Class) {
				continue
			}
			return nil, err
		}
		if im.Mode == "" || (!p.allowZero && (im.Width <= 0 || im.Height <= 0)) {
			// ImageFile.__init__: "not identified by this driver".
			continue
		}
		if err := checkDecompressionBomb(im.Width, im.Height); err != nil {
			return nil, err
		}
		if !p.decoded {
			return nil, ErrFormatNotPorted
		}
		return im, nil
	}
	return nil, unidentified()
}

// checkDecompressionBomb is Image._decompression_bomb_check.
func checkDecompressionBomb(width, height int) error {
	w, h := int64(max(1, width)), int64(max(1, height))
	// The product can exceed uint64 for bogus headers (PPM tokens are not
	// capped), so compare by division first and format the exact product.
	limit := int64(2 * maxImagePixels)
	if w > limit/h || h > limit/w {
		pixels := new(big.Int).Mul(big.NewInt(w), big.NewInt(h))
		return &Error{
			Class: "DecompressionBombError",
			Text: fmt.Sprintf("Image size (%s pixels) exceeds limit of %d pixels, "+
				"could be decompression bomb DOS attack.", pixels.String(), limit),
		}
	}
	return nil
}

// newImage builds an Image with the fields every plugin sets.
func newImage(data []byte, format string, mode string, width, height int) *Image {
	return &Image{Format: format, Mode: mode, Width: width, Height: height, data: data}
}

// setPaletteRGB stores a palette given as consecutive RGB (or already realized
// RGB) bytes and realizes it the way ImagingPalette does: 256 triples, zero
// where the palette is short.
func (im *Image) setPaletteRGB(raw []byte, rgb []byte) {
	im.setPaletteCounted(raw, rgb, len(raw)/3)
}

// setPaletteCounted stores a palette whose raw form holds count entries.
func (im *Image) setPaletteCounted(raw []byte, rgb []byte, count int) {
	im.hasPal = true
	im.palette = raw
	im.palInvalid = count > 256
	im.palRGB = [768]byte{}
	// Pillow copies whole RGB triples; a trailing partial triple is dropped.
	if len(rgb) > len(im.palRGB) {
		rgb = rgb[:len(im.palRGB)]
	}
	rgb = rgb[:len(rgb)/3*3]
	copy(im.palRGB[:], rgb)
	im.palAlpha = [256]byte{}
	for i := range im.palAlpha {
		im.palAlpha[i] = 255
	}
}

// setPaletteAlphas is Image image.putpalettealphas(data).
func (im *Image) setPaletteAlphas(data []byte) {
	for i, a := range data {
		if i >= len(im.palAlpha) {
			break
		}
		im.palAlpha[i] = a
	}
}

// setPaletteAlpha is Image image.putpalettealpha(index, alpha).
func (im *Image) setPaletteAlpha(index, alpha int) {
	if index >= 0 && index < len(im.palAlpha) {
		im.palAlpha[index] = byte(alpha)
	}
}

// Transparent is ImageReader.getTransparent() on the opened image (before any
// convert): nil, or the 3 bytes palette[t*3:t*3+3] when info['transparency'] is
// an int t and the image has a palette. A transparency that is not an int (or
// a palette that has no raw bytes) yields nil, which is also what Python
// returns when the palette lookup raises (getTransparent has no error path in
// this API).
func (im *Image) Transparent() []byte {
	if !im.transparency.present || !im.transparency.isInt {
		return nil
	}
	if len(im.palette) == 0 {
		return nil
	}
	t := im.transparency.index * 3
	// Python slicing clamps the bounds; the result is an empty bytes object
	// when the index is past the palette.
	if t < 0 {
		t = 0
	}
	if t >= len(im.palette) {
		return []byte{}
	}
	end := min(t+3, len(im.palette))
	out := make([]byte, end-t)
	copy(out, im.palette[t:end])
	return out
}

// RGBData is ImageReader.getRGBData(): it loads the pixels and converts them
// as reportlab does. mode is the resulting mode ("L", "RGB" or "CMYK"), data
// im.tobytes(), alpha nil when none.
func (im *Image) RGBData() (mode string, data, alpha []byte, err error) {
	if err := im.load(); err != nil {
		return "", nil, nil, err
	}
	switch im.Mode {
	case "LA", "RGBA":
		var band []byte
		if im.Mode == "RGBA" {
			band = im.band(3)
		} else {
			band = im.band(1)
		}
		if im.Mode == "LA" {
			return "L", im.band(0), band, nil
		}
		return "RGB", im.rgbFromRGBA(), band, nil
	case "L", "RGB", "CMYK":
		return im.Mode, im.tobytes(), nil, nil
	}
	if im.Format == "PNG" && im.Mode == "P" && im.transparency.present {
		rgba := im.rgbaFromP()
		a := make([]byte, im.Width*im.Height)
		rgb := make([]byte, 3*im.Width*im.Height)
		for i := 0; i < im.Width*im.Height; i++ {
			rgb[3*i+0] = rgba[4*i+0]
			rgb[3*i+1] = rgba[4*i+1]
			rgb[3*i+2] = rgba[4*i+2]
			a[i] = rgba[4*i+3]
		}
		return "RGB", rgb, a, nil
	}
	rgb, err := im.convertRGB()
	if err != nil {
		return "", nil, nil, err
	}
	return "RGB", rgb, nil, nil
}

// band returns one band of an image whose samples are interleaved bytes.
func (im *Image) band(n int) []byte {
	b := im.samplesPerPixel()
	out := make([]byte, im.Width*im.Height)
	for i := range out {
		out[i] = im.pix[i*b+n]
	}
	return out
}

func (im *Image) samplesPerPixel() int {
	switch im.Mode {
	case "LA":
		return 2
	case "RGBA", "CMYK":
		return 4
	case "RGB":
		return 3
	}
	return 1
}

// tobytes is Image.tobytes() for the modes reportlab keeps unchanged.
func (im *Image) tobytes() []byte {
	n := im.samplesPerPixel()
	out := make([]byte, im.Width*im.Height*n)
	copy(out, im.pix[:len(out)])
	return out
}

// decodeError converts a decoder errcode into the OSError
// _get_oserror produces.
func decoderError(code int) *Error {
	msg := map[int]string{
		-1: "buffer overrun",
		-2: "broken data stream",
		-3: "unrecognized data stream contents",
		-8: "codec configuration error",
		-9: "out of memory",
	}[code]
	if msg == "" {
		msg = fmt.Sprintf("decoder error %d", code)
	}
	return osError(msg + " when reading image file")
}

// truncatedError is the OSError ImageFile.load raises when the stream ends
// while the decoder still waits for data.
func truncatedError(notProcessed int) *Error {
	return osError(fmt.Sprintf("image file is truncated (%d bytes not processed)", notProcessed))
}

// load decodes the pixels once, in the mode Pillow reports.
func (im *Image) load() error {
	if im.pix != nil || im.pix16 != nil || im.pixI != nil || im.pixF != nil {
		return nil
	}
	if im.Mode == "P" && im.palInvalid {
		return valueError("invalid palette size")
	}
	var err error
	switch im.Format {
	case "PNG":
		err = im.loadPNG()
	case "GIF":
		err = im.loadGIF()
	case "BMP", "DIB":
		err = im.loadBMP()
	case "PPM":
		err = im.loadPPM()
	default:
		return ErrFormatNotPorted
	}
	return err
}
