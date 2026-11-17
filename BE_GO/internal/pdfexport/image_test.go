package pdfexport

import (
	"bytes"
	"encoding/base64"
	"errors"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilimage"
)

// The JPEGs of the tests, reportlab's own 8x6 RGB image (
// PIL.Image.save(io.BytesIO(), 'JPEG') of a solid red picture):
//
//	jpegImage    the file as Pillow writes it
//	jpegEntropy  the same file cut at the first entropy-coded byte: Pillow
//	             opens it and its load raises the truncated-file OSError
//	jpegCut1     the same file without its last byte: Pillow's message counts
//	             the nine entropy-coded bytes its decoder leaves unprocessed
//	jpegApp0     the file with a four-byte APP0 segment before the JFIF one:
//	             Pillow decodes it, readJPEGInfo raises on it, so reportlab
//	             embeds its canned 24x23 image
var (
	jpegImage   = decodeBase64("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDkKKKK8U/TD//Z")
	jpegEntropy = decodeBase64("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwA=")
	jpegCut1    = decodeBase64("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDkKKKK8U/TD/8=")
	jpegApp0    = decodeBase64("/9j/4AAGSkZYWP/gABBKRklGAAEBAAABAAEAAP/bAEMACAYGBwYFCAcHBwkJCAoMFA0MCwsMGRITDxQdGh8eHRocHCAkLicgIiwjHBwoNyksMDE0NDQfJzk9ODI8LjM0Mv/bAEMBCQkJDAsMGA0NGDIhHCEyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMv/AABEIAAYACAMBIgACEQEDEQH/xAAfAAABBQEBAQEBAQAAAAAAAAAAAQIDBAUGBwgJCgv/xAC1EAACAQMDAgQDBQUEBAAAAX0BAgMABBEFEiExQQYTUWEHInEUMoGRoQgjQrHBFVLR8CQzYnKCCQoWFxgZGiUmJygpKjQ1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4eLj5OXm5+jp6vHy8/T19vf4+fr/xAAfAQADAQEBAQEBAQEBAAAAAAAAAQIDBAUGBwgJCgv/xAC1EQACAQIEBAMEBwUEBAABAncAAQIDEQQFITEGEkFRB2FxEyIygQgUQpGhscEJIzNS8BVictEKFiQ04SXxFxgZGiYnKCkqNTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqCg4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2dri4+Tl5ufo6ery8/T19vf4+fr/2gAMAwEAAhEDEQA/AOQooorxT9MP/9k=")
)

func decodeBase64(text string) []byte {
	data, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		panic(err)
	}
	return data
}

// reportlab 4.2.5, canvas.drawImage(ImageReader(BytesIO(jpegApp0)), 10, 10,
// 8, 6, mask='auto'): readJPEGInfo raises on the short APP0 segment, so
// PDFImageXObject keeps the 24x23 one-bit image and PDFArray('auto') as its
// /Mask. This stream has no ASCII85 terminator (it is the canned encoded
// text), so the golden standard cannot record it.
const reportlabCannedObject = "<<\n/BitsPerComponent 1 /ColorSpace /DeviceGray /Filter [ /ASCII85Decode ] " +
	"/Height 23 /Length 223 /Mask [ a u t o ] \n  /Subtype /Image /Type /XObject /Width 24\n>>\nstream\n" +
	"\n            003B00 002700 002480 0E4940 114920 14B220 3CB650\n" +
	"            75FE88 17FF8C 175F14 1C07E2 3803C4 703182 F8EDFC\n" +
	"            B2BBC2 BB6F84 31BFC2 18EA3C 0E3E00 07FC00 03F800\n" +
	"            1E1800 1FF800>\n            endstream\n"

func TestCannedJPEGObject(t *testing.T) {
	reader, err := newImageReader(jpegApp0, bytesIdentity)
	if err != nil {
		t.Fatal(err)
	}
	c := newCanvas(595.2756, 841.8898)
	c.drawImage(reader, 8, 6)
	c.showPage()
	document, err := c.save(time.Unix(946684800, 0))
	if err != nil {
		t.Fatal(err)
	}
	text := string(document)
	start := strings.Index(text, "<<\n/BitsPerComponent 1")
	end := strings.Index(text[start:], "endstream\n")
	if start < 0 || end < 0 {
		t.Fatalf("no image object in %q", text)
	}
	if got := text[start : start+end+len("endstream\n")]; got != reportlabCannedObject {
		t.Fatalf("object\n%q\nwant\n%q", got, reportlabCannedObject)
	}
}

// objectBody is the body of object number in a reportlab file: from after its
// "N 0 obj\n" to before its "endobj\n".
func objectBody(t *testing.T, document []byte, number int) string {
	t.Helper()
	marker := []byte("\n" + strconv.Itoa(number) + " 0 obj\n")
	start := bytes.Index(document, marker)
	if start < 0 {
		t.Fatalf("no object %d", number)
	}
	start += len(marker)
	end := bytes.Index(document[start:], []byte("endobj\n"))
	if end < 0 {
		t.Fatalf("object %d has no end", number)
	}
	return string(document[start : start+end])
}

// An image takes its place in reportlab's object numbering beside the colour
// port's resources: with an RGBA image, its soft mask, a separation colour
// space, its tint function and alpha text on one page, reportlab numbers
// 1 font dictionary, 2-3 fonts, 4 image, 5 soft mask, 6 separation, 7 page,
// 8 catalog, 9 info, then the format pass's 10 tint function, 11 page tree and
// 12 page stream. Every dictionary, reference and resource entry below is
// reportlab 4.2.5's for the same document (the image's name is the md5 of the
// pixels it decodes).
func TestImageAndColourObjectNumbers(t *testing.T) {
	// A 4x3 RGBA PNG, the source of the img-png-rgba golden case.
	png := "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFElEQVR4nGM8ISfXwIAEmJA5WAUASroBinVI9KIAAAAASUVORK5CYII="
	document, err := (ReportTable{Headers: []string{"A"}, Rows: [][]string{{"x"}}, Title: "T",
		TextBefore: `<font color="rgba(255,0,0,0.5)">alfa</font> <img src="` + png + `"/> ` +
			`<font color="CMYKColorSep(0,1,0,0,spotName='SPOT')">spot</font> ` +
			`<font color="cmyk(0,0,0,1)">nero</font> fine`}).Build(time.Unix(946684800, 0))
	if err != nil {
		t.Fatal(err)
	}
	image := objectBody(t, document, 4)
	if !strings.Contains(image, "/Subtype /Image") || !strings.Contains(image, "/SMask 5 0 R") {
		t.Fatalf("object 4: %q", image)
	}
	smask := objectBody(t, document, 5)
	if !strings.Contains(smask, "/ColorSpace /DeviceGray") || !strings.Contains(smask, "/Decode [ 0 1 ]") {
		t.Fatalf("object 5: %q", smask)
	}
	if got, want := objectBody(t, document, 6), "[ /Separation /SPOT /DeviceCMYK 10 0 R ]\n"; got != want {
		t.Fatalf("object 6: %q, want %q", got, want)
	}
	page := objectBody(t, document, 7)
	for _, want := range []string{"/Contents 12 0 R", "/Parent 11 0 R", "/ColorSpace <<\n/SPOT 6 0 R\n>>",
		"/gRLs0 <<\n/ca .5\n>> /gRLs1 <<\n/ca 1\n>>", "/XObject <<\n/FormXob.", " 4 0 R\n>>"} {
		if !strings.Contains(page, want) {
			t.Fatalf("object 7 lacks %q: %q", want, page)
		}
	}
	if !strings.Contains(objectBody(t, document, 10), "/FunctionType 4") {
		t.Fatalf("object 10 is not the tint function")
	}
	if !strings.Contains(objectBody(t, document, 11), "/Type /Pages") {
		t.Fatalf("object 11 is not the page tree")
	}
	if !strings.Contains(objectBody(t, document, 12), "/Filter [ /ASCII85Decode /FlateDecode ]") {
		t.Fatalf("object 12 is not the page stream")
	}
}

// jpegDecode is the decode getRGBData performs on a JPEG (X19): the 8x6 image
// loads, the file cut at the entropy-coded data raises Pillow's truncated-file
// OSError with the number Pillow 13.0.0 gives for these two inputs.
func TestJPEGDecodeOutcome(t *testing.T) {
	if err := jpegDecode(jpegImage); err != nil {
		t.Fatalf("complete image: %v", err)
	}
	for name, item := range map[string]struct {
		data []byte
		text string
	}{
		"entropy cut": {jpegEntropy, "image file is truncated (0 bytes not processed)"},
		"cut one":     {jpegCut1, "image file is truncated (9 bytes not processed)"},
	} {
		err := jpegDecode(item.data)
		var pillow *pilimage.Error
		if err == nil || !errors.As(err, &pillow) || pillow.Class != "OSError" || pillow.Text != item.text {
			t.Fatalf("%s: %v, want OSError %q", name, err, item.text)
		}
	}
}

// readJPEGInfo reads the file's dimensions and components and rejects what
// reportlab's reader rejects.
func TestReadJPEGInfo(t *testing.T) {
	if width, height, components, ok := readJPEGInfo(jpegImage); !ok || width != 8 || height != 6 || components != 3 {
		t.Fatalf("jpegImage: %d %d %d %v", width, height, components, ok)
	}
	if _, _, _, ok := readJPEGInfo(jpegApp0); ok {
		t.Fatal("the short APP0 segment was accepted")
	}
	// The SOF marker reportlab's reader accepts (baseline, extended sequential
	// and progressive), the ones it rejects and the precision it insists on.
	sof := bytes.Index(jpegImage, []byte{0xFF, 0xC0})
	if sof < 0 {
		t.Fatal("no SOF0 marker")
	}
	patched := func(offset int, value byte) []byte {
		data := append([]byte(nil), jpegImage...)
		data[sof+offset] = value
		return data
	}
	if _, _, components, ok := readJPEGInfo(patched(1, 0xC1)); !ok || components != 3 {
		t.Fatalf("SOF1: %v", ok)
	}
	for name, data := range map[string][]byte{
		"arithmetic SOF9": patched(1, 0xC9),
		"12-bit":          patched(4, 12),
	} {
		if _, _, _, ok := readJPEGInfo(data); ok {
			t.Fatalf("%s was accepted", name)
		}
	}
	if _, _, components, ok := readJPEGInfo(patched(9, 4)); !ok || components != 4 {
		t.Fatalf("four components: %d %v", components, ok)
	}
}

// The annotations reportlab puts on the two failures an image source has: an
// ImageReader that Pillow cannot open (the paragraph's exception, with the
// reader's fileName and identity) and a draw whose getRGBData raises (the
// identity only). Addresses are pystr.ObjectAddress (X18).
func TestImageFailureTexts(t *testing.T) {
	style := paraStyle{fontName: "Helvetica", fontSize: numInt(8)}
	_, _, _, err := parseParagraph(`a <img src="data:image/png;base64,aGVsbG8="/> b`, style)
	var failure *LayoutError
	if !errors.As(err, &failure) {
		t.Fatalf("an unidentifiable source: %v", err)
	}
	want := "\nparagraph text '<para>a <img src=\"data:image/png;base64,aGVsbG8=\"/> b</para>' caused exception " +
		"\nfileName='data:image/png;base64,aGVsbG8=' identity=[ImageReader@0x7f0000000000 " +
		"filename='data:image/png;base64,aGVsbG8='] cannot identify image file <_io.BytesIO object at 0x7f0000000000>"
	if failure.Text != want {
		t.Fatalf("text %q, want %q", failure.Text, want)
	}

	src := "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(jpegEntropy)
	_, err = (ReportTable{Headers: []string{"A"}, Rows: [][]string{{"x"}}, Title: "T",
		TextBefore: `a <img src="` + src + `"/> b`}).Build(time.Unix(946684800, 0))
	if !errors.As(err, &failure) {
		t.Fatalf("a truncated inline JPEG: %v", err)
	}
	want = "\nidentity=[ImageReader@0x7f0000000000 filename='" + src + "'] image file is truncated (0 bytes not processed)"
	if failure.Text != want {
		t.Fatalf("text %q, want %q", failure.Text, want)
	}

	header := `<img src="` + src + `" style="height: 1000px;"/><h1>x</h1><h2>y</h2><h3>z</h3>`
	_, err = DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T", Header: &header,
		IsA3: true, Portrait: true}.Build(time.Unix(946684800, 0))
	if !errors.As(err, &failure) {
		t.Fatalf("a truncated logo: %v", err)
	}
	want = "\nidentity=[ImageReader@0x7f0000000000] image file is truncated (0 bytes not processed)"
	if failure.Text != want {
		t.Fatalf("text %q, want %q", failure.Text, want)
	}
}
