package medicalform

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"image"
	"image/color"
	"image/png"
	"math"
	"strconv"
	"strings"
	"testing"
)

// A signature image with transparency: 240 x 120, a blue stroke over nothing.
func signaturePNG(t *testing.T) []byte {
	t.Helper()
	picture := image.NewNRGBA(image.Rect(0, 0, 240, 120))
	for y := 20; y < 100; y++ {
		for x := 10; x < 230; x++ {
			if (x+y)%17 < 6 {
				picture.SetNRGBA(x, y, color.NRGBA{R: 20, G: 40, B: 160, A: 255})
			}
		}
	}
	var data bytes.Buffer
	if err := png.Encode(&data, picture); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func dataURI(file []byte) string {
	return "  data:image/png;base64," + base64.StdEncoding.EncodeToString(file)
}

// The images fill_pdf_fields draws with PyMuPDF: the signature always, the
// stamp when it opens. A failing signature skips both.
func TestFillImages(t *testing.T) {
	signature := dataURI(signaturePNG(t))
	values := map[string]string{SignatureKey: signature}
	images := FillImages(values)
	if len(images) != 1 || images[0].rect != (Rect{X0: 400, Y0: 20, X1: 520, Y1: 1300}) {
		t.Fatalf("signature only: %+v", images)
	}
	values[StampKey] = signature
	images = FillImages(values)
	if len(images) != 2 || images[1].rect != (Rect{X0: 100, Y0: 20, X1: 260, Y1: 1300}) {
		t.Fatalf("with stamp: %+v", images)
	}
	// fill_pdf_fields' stamp try block: a file MuPDF cannot open skips the stamp alone.
	values[StampKey] = "  data:image/png;base64,AAAA"
	if images = FillImages(values); len(images) != 1 {
		t.Fatalf("bad stamp: %+v", images)
	}
	// The signature's try block wraps everything: no image at all.
	values[SignatureKey] = "  data:image/png;base64,AAAA"
	if images = FillImages(values); len(images) != 0 {
		t.Fatalf("bad signature: %+v", images)
	}
	delete(values, SignatureKey)
	if images = FillImages(values); len(images) != 0 {
		t.Fatalf("no signature key: %+v", images)
	}
}

// The overlay is an incremental update of FillBiella's own output: the filled
// form as it is, then one image XObject (and its mask), one content stream
// drawing it with PyMuPDF's matrix, the page object and a chained xref.
func TestFillBiellaWithImagesStructure(t *testing.T) {
	values := map[string]string{
		"sport_association.denomination": "  POLISPORTIVA",
		"sport_association.federation":   "  FIDAL",
		SignatureKey:                     dataURI(signaturePNG(t)),
	}
	plain := FillBiella(values)
	filled := FillBiellaWithImages(values, FillImages(values))
	if !bytes.HasPrefix(filled, plain) || len(filled) <= len(plain) {
		t.Fatalf("overlay did not start from the filled form (%d of %d bytes)", len(filled), len(plain))
	}
	added := filled[len(plain):]
	if count := bytes.Count(added, []byte("/Subtype /Image")); count != 2 {
		t.Errorf("%d images in the update, want the image and its mask", count)
	}
	if !bytes.Contains(added, []byte("/ColorSpace /DeviceRGB")) || !bytes.Contains(added, []byte("/BitsPerComponent 1")) {
		t.Errorf("image and mask dictionaries missing in %q", added)
	}
	// PyMuPDF's Page.insert_image matrix for a 240 x 120 image in (400, 20, 520, 1300).
	for index, want := range []float64{120, 0, 0, 60, 400, 151.92} {
		if got := matrixNumber(added, index); math.Abs(got-want) > 0.001 {
			t.Errorf("matrix value %d of the content stream is %v, want %v", index, got, want)
		}
	}
	if !bytes.Contains(added, []byte("/fzImg0 Do")) || !bytes.Contains(added, []byte(" /fzImg0 ")) {
		t.Errorf("image not named in the content stream and the resources: %q", added)
	}
	if !bytes.Contains(added, []byte("/Prev ")) || !bytes.Contains(added, []byte("startxref")) {
		t.Errorf("no chained cross-reference section: %q", added)
	}
	// The form without an image keeps the exact bytes.
	if withImages := FillBiellaWithImages(values, nil); !bytes.Equal(withImages, plain) {
		t.Error("no image must be the plain filled form")
	}
}

// matrixNumber is the index-th operand of the first "cm" operator in data.
func matrixNumber(data []byte, index int) float64 {
	at := bytes.Index(data, []byte(" cm\n"))
	if at < 0 {
		return math.NaN()
	}
	start := bytes.LastIndexByte(data[:at], '\n') + 1
	fields := strings.Fields(string(data[start:at]))
	if len(fields) != 6 {
		return math.NaN()
	}
	value, err := strconv.ParseFloat(fields[index], 64)
	if err != nil {
		return math.NaN()
	}
	return value
}

// imageMatrix is PyMuPDF's calc_image_matrix(w, h, clip, 0, True); the expected
// matrices are that function's result on the form's first page.
func TestImageMatrix(t *testing.T) {
	box := [4]float64{0, 0, 595.2, 841.92}
	for _, item := range []struct {
		width, height int
		rect          Rect
		want          [6]float64
	}{
		{240, 120, Rect{400, 20, 520, 1300}, [6]float64{120, 0, 0, 60, 400, 151.92}},
		{240, 120, Rect{100, 20, 260, 1300}, [6]float64{160, 0, 0, 80, 100, 141.92}},
		{100, 400, Rect{400, 20, 520, 1300}, [6]float64{120, 0, 0, 480, 400, -58.08}},
		{300, 300, Rect{0, 0, 100, 100}, [6]float64{100, 0, 0, 100, 0, 741.91998}},
		{50, 20, Rect{400, 20, 520, 300}, [6]float64{120, 0, 0, 48, 400, 657.92}},
		{1000, 10, Rect{300, 100, 500, 200}, [6]float64{200, 0, 0, 2, 300, 690.92}},
	} {
		got := imageMatrix(item.width, item.height, item.rect, box, box, 0)
		for i := range got {
			if math.Abs(got[i]-item.want[i]) > 0.001 {
				t.Errorf("image %dx%d in %v: matrix %v, want %v", item.width, item.height, item.rect, got, item.want)
				break
			}
		}
	}
}

// a2bBase64 is base64.b64decode of a str (Python 3.13.16), measured on that
// interpreter: padding is needed for an incomplete final quad, characters
// outside the alphabet are skipped, a byte outside ASCII is an error.
func TestA2BBase64(t *testing.T) {
	for _, item := range []struct {
		text string
		want string
		ok   bool
	}{
		{"", "", true},
		{"AAAA", "000000", true},
		{"AAA=", "0000", true},
		{"AA==", "00", true},
		{"AAAA=", "000000", true},
		{"AAA==", "0000", true},
		{"====", "", true},
		{"!!!!", "", true},
		{"-_-_", "", true},
		{"AA!AA", "000000", true},
		{"  AAAA", "000000", true},
		{"AA==AA==", "000000", true},
		{"QUJDRA===", "41424344", true},
		{"A", "", false},
		{"AA", "", false},
		{"AAA", "", false},
		{"A===", "", false},
		{"AA=A", "", false},
		{"AAA=AAAA", "", false},
		{"QUJDRA=", "", false},
		{"AAAAAA", "", false},
	} {
		got, err := a2bBase64([]byte(item.text))
		if (err == nil) != item.ok || hex.EncodeToString(got) != item.want {
			t.Errorf("a2bBase64(%q) = %x, %v; want %s, ok=%v", item.text, got, err, item.want, item.ok)
		}
	}
	if _, err := a2bBase64([]byte("AAé")); err == nil {
		t.Error("a non-ASCII byte is an error, as base64.b64decode's ASCII encoding is")
	}
	// imageFile cuts at the first ';base64,' like str.split(...)[1].
	if file, ok := imageFile("  data:image/png;base64,QUJD"); !ok || string(file) != "ABC" {
		t.Errorf("imageFile of a data URI: %q, %v", file, ok)
	}
	if file, ok := imageFile("  QUJD"); !ok || string(file) != "ABC" {
		t.Errorf("imageFile of a plain value: %q, %v", file, ok)
	}
}
