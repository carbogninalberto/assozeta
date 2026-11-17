package pdfexport

import (
	"errors"
	"regexp"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilimage"
)

var (
	headerImage    = regexp.MustCompile(`src="data:image/jpeg;base64,([^"]+)"`)
	headerTitle    = regexp.MustCompile(`<h1[^>]*>(.*?)</h1>`)
	headerSubtitle = regexp.MustCompile(`<h2[^>]*>(.*?)</h2>`)
	headerApprove  = regexp.MustCompile(`<h3[^>]*>(.*?)</h3>`)
)

// imageFlowable is platypus.Image of a logo drawn at a fixed size.
type imageFlowable struct {
	image         *imageReader
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

// loadLogo is Django's logo block: base64.b64decode of the data URL, then
// platypus.Image(BytesIO(...)). A nil image is what Django answers with the
// '[LOGO]' paragraph: Python's base64 refuses the text, no data is left,
// Pillow refuses to open the image (its exception is caught there), the
// announced height is zero (the drawWidth division raises), or the image is one
// of Pillow's stub formats, which open at 0x0. A format pilimage only
// identifies but Pillow opens at a real size is a refusal: its decoder, and
// so the logo Pillow would draw, is not ported yet.
func loadLogo(encoded string) (*imageReader, error) {
	data, ok := pythonB64Decode(encoded)
	if !ok || len(data) == 0 {
		return nil, nil
	}
	image, err := newImageReader(data, bytesIdentity)
	if err != nil {
		var pillow *pilimage.Error
		if errors.As(err, &pillow) {
			return nil, nil // Django's try: PIL's exception answers '[LOGO]'
		}
		return nil, err
	}
	if image.image.Height == 0 {
		return nil, nil // ZeroDivisionError
	}
	return image, nil
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
		image, err := loadLogo(match[1])
		if err != nil {
			return nil, err
		}
		if image != nil {
			// img.drawHeight = 100; img.drawWidth = img.drawHeight *
			// (img.imageWidth / img.imageHeight), a Python float division.
			logo = &imageFlowable{image: image, height: 100,
				width: 100 * (float64(image.image.Width) / float64(image.image.Height))}
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
		paraStyle{fontName: "Helvetica-Bold", fontSize: numInt(20), leading: numInt(24), spaceBefore: numInt(30), spaceAfter: numInt(10)})
	if err != nil {
		return nil, err
	}
	subtitle, err := newParagraph(headerText(headerSubtitle, header, "Bilancio"),
		paraStyle{fontName: "Helvetica", fontSize: numInt(16), leading: numInt(20), spaceAfter: numInt(10)})
	if err != nil {
		return nil, err
	}
	approve, err := newParagraph(headerText(headerApprove, header, "Approvato il __/__/____"),
		paraStyle{fontName: "Helvetica", fontSize: numInt(16), leading: numInt(20)})
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
	return newParagraph(text, paraStyle{fontName: "Helvetica", fontSize: numInt(11), leading: numInt(11), centred: true, spaceBefore: numInt(10)})
}
