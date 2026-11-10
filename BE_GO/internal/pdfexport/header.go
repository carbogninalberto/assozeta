package pdfexport

import (
	"bytes"
	"crypto/md5"
	"encoding/hex"
	imagecolor "image/color"
	"image/jpeg"
	"regexp"
	"strings"
)

var (
	headerImage    = regexp.MustCompile(`src="data:image/jpeg;base64,([^"]+)"`)
	headerTitle    = regexp.MustCompile(`<h1[^>]*>(.*?)</h1>`)
	headerSubtitle = regexp.MustCompile(`<h2[^>]*>(.*?)</h2>`)
	headerApprove  = regexp.MustCompile(`<h3[^>]*>(.*?)</h3>`)
)

// imageFlowable is platypus.Image of a JPEG drawn at a fixed size.
type imageFlowable struct {
	image         *jpegImage
	width, height float64
}

func (i *imageFlowable) wrap(*canvas, float64, float64) (float64, float64) { return i.width, i.height }
func (i *imageFlowable) split(*canvas, float64, float64) []flowable        { return nil }
func (i *imageFlowable) draw(c *canvas)                                    { c.drawImage(i.image, i.width, i.height) }
func (i *imageFlowable) spaceBefore() float64                              { return 0 }
func (i *imageFlowable) spaceAfter() float64                               { return 0 }
func (i *imageFlowable) hAlign() string                                    { return "CENTER" }

// pythonB64Decode is base64.b64decode(text) without validation: characters
// outside the alphabet are skipped, a complete padding ends the data, and an
// unfinished group is binascii.Error (false).
func pythonB64Decode(text string) ([]byte, bool) {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	var out []byte
	position, pads := 0, 0
	left := byte(0)
	for _, r := range text {
		if r >= 0x80 {
			return nil, false // string argument should contain only ASCII characters
		}
		if r == '=' {
			if position >= 2 {
				if pads++; position+pads >= 4 {
					return out, true
				}
			}
			continue
		}
		value := strings.IndexRune(alphabet, r)
		if value < 0 {
			continue
		}
		pads = 0
		ch := byte(value)
		switch position {
		case 0:
			position, left = 1, ch
		case 1:
			position, left, out = 2, ch&0x0f, append(out, left<<2|ch>>4)
		case 2:
			position, left, out = 3, ch&0x03, append(out, left<<4|ch>>2)
		case 3:
			position, left, out = 0, 0, append(out, left<<6|ch)
		}
	}
	return out, position == 0
}

// loadJPEG reads the logo reportlab embeds as it is (DCTDecode). reportlab
// names the image object after a digest of the pixels PIL decodes, which
// another decoder does not reproduce bit for bit: the name here is a digest
// of the file, and the parity standard compares image names by position.
// A nil image is data Django's view answers with the '[LOGO]' paragraph
// (base64 Python refuses, or no data at all). Data that is not a baseline or
// progressive 8-bit JPEG this decoder reads is not ported: whether PIL reads
// it, and as what, is not reproduced.
func loadJPEG(encoded string) (*jpegImage, error) {
	data, ok := pythonB64Decode(encoded)
	if !ok || len(data) == 0 {
		return nil, nil
	}
	config, err := jpeg.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, ErrNotPorted
	}
	if _, err := jpeg.Decode(bytes.NewReader(data)); err != nil {
		return nil, ErrNotPorted
	}
	space, inverted := "", false
	switch config.ColorModel {
	case imagecolor.GrayModel:
		space = "DeviceGray"
	case imagecolor.YCbCrModel, imagecolor.RGBAModel:
		space = "DeviceRGB"
	case imagecolor.CMYKModel:
		// Four components: reportlab adds the inverting /Decode array.
		space, inverted = "DeviceCMYK", true
	default:
		return nil, ErrNotPorted
	}
	if config.Width <= 0 || config.Height <= 0 {
		return nil, ErrNotPorted
	}
	digest := md5.Sum(data)
	return &jpegImage{name: "FormXob." + hex.EncodeToString(digest[:]), data: data, width: config.Width, height: config.Height,
		colorSpace: space, inverted: inverted}, nil
}

func headerText(pattern *regexp.Regexp, header, fallback string) string {
	if match := pattern.FindStringSubmatch(header); match != nil {
		return match[1]
	}
	return fallback
}

// balanceHeader is the header table of generate_pdf_from_data: the logo (or
// the '[LOGO]' paragraph) and the three paragraphs read from the HTML header.
func balanceHeader(header string) (flowable, error) {
	var logo flowable
	if match := headerImage.FindStringSubmatch(header); match != nil {
		image, err := loadJPEG(match[1])
		if err != nil {
			return nil, err
		}
		if image != nil {
			logo = &imageFlowable{image: image, height: 100, width: 100 * (float64(image.width) / float64(image.height))}
		}
	}
	if logo == nil {
		paragraph, err := newParagraph("[LOGO]", styleNormal())
		if err != nil {
			return nil, err
		}
		logo = paragraph
	}
	title, err := newParagraph(headerText(headerTitle, header, "Associazione Sportiva"),
		paraStyle{fontName: "Helvetica-Bold", fontSize: 20, leading: 24, spaceBefore: 30, spaceAfter: 10})
	if err != nil {
		return nil, err
	}
	subtitle, err := newParagraph(headerText(headerSubtitle, header, "Bilancio"),
		paraStyle{fontName: "Helvetica", fontSize: 16, leading: 20, spaceAfter: 10})
	if err != nil {
		return nil, err
	}
	approve, err := newParagraph(headerText(headerApprove, header, "Approvato il __/__/____"),
		paraStyle{fontName: "Helvetica", fontSize: 16, leading: 20})
	if err != nil {
		return nil, err
	}
	wide := sizeA2[1]
	t := newTable([][]tableCell{{{}, {list: true, flows: []flowable{logo}}, {list: true, flows: []flowable{title, subtitle, approve}}, {}}},
		[]float64{wide / 4, wide / 8, (wide / 8) * 3, wide / 4}, 0)
	t.each(0, 0, -1, -1, func(s *cellStyle) { s.valign = "TOP" })
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.topPadding = 20 })
	t.each(0, 0, 0, 0, func(s *cellStyle) { s.alignment = "RIGHT" })
	t.each(1, 0, 1, 0, func(s *cellStyle) { s.alignment = "RIGHT" })
	t.each(2, 0, 2, 0, func(s *cellStyle) { s.alignment = "LEFT" })
	t.each(3, 0, 3, 0, func(s *cellStyle) { s.alignment = "LEFT" })
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.bottomPadding = 20 })
	return t, nil
}

// additionalParagraph is one additional_data paragraph: Helvetica 11 on 11,
// centred, 10 points above.
func additionalParagraph(text string) (flowable, error) {
	return newParagraph(text, paraStyle{fontName: "Helvetica", fontSize: 11, leading: 11, centred: true, spaceBefore: 10})
}
