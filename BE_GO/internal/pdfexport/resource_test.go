package pdfexport

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"image"
	imagecolor "image/color"
	"image/jpeg"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// jpegSource is a small JPEG, the image an image source carries.
var jpegSource = func() []byte {
	picture := image.NewRGBA(image.Rect(0, 0, 24, 16))
	picture.Set(0, 0, imagecolor.RGBA{R: 9, G: 200, B: 30, A: 255})
	var out bytes.Buffer
	if err := jpeg.Encode(&out, picture, nil); err != nil {
		panic(err)
	}
	return out.Bytes()
}()

// The sources reportlab's open_for_read reads (lib/utils.py): the name as a
// path first, then a data: URL through datareader, else urlopen. Every
// expectation is the outcome reportlab 4.2.5 gives for the same name under the
// default rl_settings (trustedHosts None): the bytes it reads, or a failure.
func TestDefaultResourceSources(t *testing.T) {
	directory := t.TempDir()
	logo := filepath.Join(directory, "logo.jpg")
	if err := os.WriteFile(logo, jpegSource, 0o644); err != nil {
		t.Fatal(err)
	}
	t.Chdir(directory)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/logo.jpg":
			w.Header().Set("Content-Type", "image/jpeg")
			w.Write(jpegSource)
		case "/redirect":
			http.Redirect(w, r, "/logo.jpg", http.StatusFound)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	encoded := base64.StdEncoding.EncodeToString(jpegSource)
	if err := os.WriteFile(filepath.Join(directory, "a b.jpg"), []byte("SPACE"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(directory, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "sub", "c d.jpg"), []byte("SUB"), 0o644); err != nil {
		t.Fatal(err)
	}
	accepted := map[string][]byte{
		"logo.jpg":                            jpegSource,
		logo:                                  jpegSource,
		"file:logo.jpg":                       jpegSource,
		"file://" + logo:                      jpegSource,
		"file:/" + filepath.ToSlash(logo)[1:]: jpegSource,
		"file://localhost" + logo:             jpegSource,
		"file:./a%20b.jpg":                    []byte("SPACE"),
		"file:sub/c%20d.jpg":                  []byte("SUB"),
		"file://" + directory + "/a%20b.jpg":  []byte("SPACE"),
		server.URL + "/logo.jpg":              jpegSource,
		server.URL + "/redirect":              jpegSource,
		"data:image/jpeg;base64," + encoded:   jpegSource,
		"data:;base64," + encoded:             jpegSource,
		"DATA:;base64," + encoded:             jpegSource,
		"data:image/jpeg%;base64," + encoded:  jpegSource,
		"data:a=b;base64," + encoded:          jpegSource,
		"data:,hello":                         []byte("hello"),
		"data:;base64,aGVsbG8=":               []byte("hello"),
		"data:,%41%42":                        []byte("AB"),
		"data:,a%20b":                         []byte("a b"),
		"data:,%C3%A9":                        {0xE9},
		"data:,citt%C3%A0":                    []byte("citt\xe0"),
		"data:text/plain,hello%2Cworld":       []byte("hello,world"),
		"data:base64,x":                       []byte("x"),
		"data:,%":                             []byte("%"),
		"data:,%4":                            []byte("%4"),
		"data:,%zz":                           []byte("%zz"),
		"data:,100%":                          []byte("100%"),
		"data:image/jpeg;base64,!!!":          {},
		"data:image/jpeg;base64,":             {},
		"data:,":                              {},
	}
	for name, want := range accepted {
		got, err := DefaultResourceOpener{}.Open(context.Background(), name)
		if err != nil {
			t.Errorf("%q: %v", name, err)
			continue
		}
		if !bytes.Equal(got, want) {
			t.Errorf("%q: %q, want %q", name, got, want)
		}
	}

	refused := []string{
		"missing.jpg",
		filepath.Join(directory, "missing.jpg"),
		"nonsense",
		"gopher://127.0.0.1/logo.jpg",
		server.URL + "/missing",
		"file://" + directory + "/missing.jpg",
		"file://remote.test" + logo,
		"file:",
		"file://" + directory,
		"data:image/jpeg;base64",
		"data:image/jpeg;base64,aGVsbG8",
		"data:;base64,aGVsbG8",
		"data:,%FF",
		"data:,%E9",
	}
	for _, name := range refused {
		if got, err := (DefaultResourceOpener{}).Open(context.Background(), name); err == nil {
			t.Errorf("%q: %q, want a failure", name, got)
		}
	}

	// reportlab downloads an ftp: URL with urlopen; with nothing listening the
	// fetch fails, and TestFTPImageSourceTranscripts proves the successful reads.
	if _, err := (DefaultResourceOpener{}).Open(context.Background(), "ftp://127.0.0.1:1/logo.jpg"); err == nil {
		t.Errorf("ftp: want a failure")
	}
}

// openResource is a ResourceOpener of fixed sources.
type openResource func(name string) ([]byte, error)

func (o openResource) Open(_ context.Context, name string) ([]byte, error) { return o(name) }

// setResource makes opener the package's resource opener for the test.
func setResource(t *testing.T, opener ResourceOpener) {
	t.Helper()
	previous := Resource
	Resource = opener
	t.Cleanup(func() { Resource = previous })
}

// A source reportlab cannot open is the export's failure, with reportlab's
// message; an opener that answers the source serves the image.
func TestImageSourceFailureAndSeam(t *testing.T) {
	setResource(t, openResource(func(string) ([]byte, error) { return nil, errors.New("no such file") }))
	_, _, _, err := parseParagraph(`a <img src="logo.jpg"/> b`, paraStyle{fontName: "Helvetica", fontSize: numInt(9), leading: numInt(12)})
	var failure *LayoutError
	if !errors.As(err, &failure) {
		t.Fatalf("a source reportlab cannot open: %v", err)
	}
	// reportlab 4.2.5: str() of the OSError, its ImageReader address as pystr.ObjectAddress (X18).
	want := "\nparagraph text '<para>a <img src=\"logo.jpg\"/> b</para>' caused exception \nfileName='logo.jpg' " +
		"identity=[ImageReader@0x7f0000000000 filename='logo.jpg'] Cannot open resource \"logo.jpg\""
	if failure.Text != want {
		t.Fatalf("text %q, want %q", failure.Text, want)
	}
	setResource(t, openResource(func(name string) ([]byte, error) {
		if name != "served.jpg" {
			t.Errorf("source %q", name)
		}
		return jpegSource, nil
	}))
	frags, _, _, err := parseParagraph(`a <img src="served.jpg"/> b`, paraStyle{fontName: "Helvetica", fontSize: numInt(9), leading: numInt(12)})
	if err != nil {
		t.Fatal(err)
	}
	var carried *imageReader
	for _, frag := range frags {
		if frag.img != nil {
			carried = frag.img.image
		}
	}
	if carried == nil || carried.image.Width != 24 || carried.image.Height != 16 || len(carried.data) == 0 {
		t.Fatalf("image %+v", carried)
	}
	// A format pilimage decodes is carried with Pillow's size; a source Pillow
	// cannot identify is the paragraph's exception, not a refusal.
	setResource(t, openResource(func(string) ([]byte, error) {
		return []byte("\x89PNG\r\n\x1a\n"), nil
	}))
	if _, _, _, err := parseParagraph(`a <img src="logo.png"/> b`, paraStyle{fontName: "Helvetica", fontSize: numInt(9)}); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
		t.Fatalf("a truncated PNG source: %v", err)
	}
	// A format pilimage only identifies stays a refusal.
	setResource(t, openResource(func(string) ([]byte, error) {
		return []byte("II*\x00\x08\x00\x00\x00\x0a\x00\x00\x01\x04\x00\x01\x00\x00\x00\x02\x00\x00\x00\x01\x01\x04\x00\x01\x00\x00\x00\x02\x00\x00\x00\x02\x01\x03\x00\x03\x00\x00\x00\x86\x00\x00\x00\x03\x01\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x06\x01\x03\x00\x01\x00\x00\x00\x02\x00\x00\x00\x11\x01\x04\x00\x01\x00\x00\x00\x8c\x00\x00\x00\x15\x01\x03\x00\x01\x00\x00\x00\x03\x00\x00\x00\x16\x01\x04\x00\x01\x00\x00\x00\x02\x00\x00\x00\x17\x01\x04\x00\x01\x00\x00\x00\x0c\x00\x00\x00\x1c\x01\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x08\x00\x08\x00\x08\x00\x01\x02\x03\x01\x02\x03\x01\x02\x03\x01\x02\x03"), nil
	}))
	if _, _, _, err := parseParagraph(`a <img src="logo.tif"/> b`, paraStyle{fontName: "Helvetica", fontSize: numInt(9)}); !errors.Is(err, ErrNotPorted) {
		t.Fatalf("a TIFF source: %v", err)
	}
}
