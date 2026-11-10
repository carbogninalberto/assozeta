package pilplaceholder

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"image"
	"image/png"
	"os"
	"testing"
)

func readGolden(t *testing.T, name string, into any) {
	t.Helper()
	file, err := os.Open("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	zr, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.NewDecoder(zr).Decode(into); err != nil {
		t.Fatal(err)
	}
}

// decodeRGB returns the size and the RGB bytes of a PNG, through Go's own
// decoder.
func decodeRGB(t *testing.T, data []byte) (int, int, []byte) {
	t.Helper()
	img, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	rgba, ok := img.(*image.RGBA)
	if !ok {
		t.Fatalf("decoded as %T, want 8-bit RGB", img)
	}
	b := rgba.Bounds()
	out := make([]byte, 0, b.Dx()*b.Dy()*3)
	for y := 0; y < b.Dy(); y++ {
		line := rgba.Pix[y*rgba.Stride : y*rgba.Stride+b.Dx()*4]
		for x := 0; x < b.Dx(); x++ {
			out = append(out, line[4*x], line[4*x+1], line[4*x+2])
		}
	}
	return b.Dx(), b.Dy(), out
}

func mix(seed, index uint64) uint64 {
	z := index + seed*0x9E3779B97F4A7C15
	z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
	z = (z ^ (z >> 27)) * 0x94D049BB133111EB
	return z ^ (z >> 31)
}

// formulaPixels mirrors formula_pixels in
// TESTS/adapters/go/pil_placeholder_golden.py. Keep in sync.
func formulaPixels(width, height int, seed, run, period, mask uint64) []byte {
	rowBytes := width * 3
	out := make([]byte, 0, rowBytes*height)
	for y := 0; y < height; y++ {
		key := uint64(y) % period
		for x := 0; x < rowBytes; x++ {
			out = append(out, byte(mix(seed, key*uint64(rowBytes)+uint64(x)/run)&mask))
		}
	}
	return out
}

func encodeRGB(width, height int, pixels []byte, z Zlib) []byte {
	return encodePNG(width, height, func(y int, rgb []byte) {
		copy(rgb, pixels[y*width*3:(y+1)*width*3])
	}, z)
}

// variants pairs each deflate implementation with the golden files Pillow
// wrote while using it.
var variants = []struct {
	name string
	zlib Zlib
}{{"zlibng", ZlibNG}, {"zlib", ZlibClassic}}

// TestPNGEncoderMatchesPillow checks the filter choice, the deflate stream and
// the IDAT chunking on images that have nothing to do with text, so an encoder
// difference is reported apart from a rendering difference.
func TestPNGEncoderMatchesPillow(t *testing.T) {
	for _, v := range variants {
		t.Run(v.name, func(t *testing.T) { testPNGEncoder(t, "png_golden_"+v.name+".json.gz", v.zlib) })
	}
}

func testPNGEncoder(t *testing.T, file string, z Zlib) {
	var golden struct {
		Cases []struct {
			PNG    string `json:"png"`
			Width  int    `json:"width"`
			Height int    `json:"height"`
			Seed   uint64 `json:"seed"`
			Run    uint64 `json:"run"`
			Period uint64 `json:"period"`
			Mask   uint64 `json:"mask"`
			SHA256 string `json:"sha256"`
			Length int    `json:"length"`
		} `json:"cases"`
	}
	readGolden(t, file, &golden)
	if len(golden.Cases) < 50 {
		t.Fatalf("only %d cases", len(golden.Cases))
	}
	multiChunk := 0
	for index, c := range golden.Cases {
		if c.PNG != "" {
			want, err := base64.StdEncoding.DecodeString(c.PNG)
			if err != nil {
				t.Fatal(err)
			}
			width, height, pixels := decodeRGB(t, want)
			got := encodeRGB(width, height, pixels, z)
			if !bytes.Equal(got, want) {
				t.Errorf("case %d (%dx%d): bytes differ", index, width, height)
			}
			continue
		}
		got := encodeRGB(c.Width, c.Height, formulaPixels(c.Width, c.Height, c.Seed, c.Run, c.Period, c.Mask), z)
		sum := sha256.Sum256(got)
		if len(got) != c.Length || hex.EncodeToString(sum[:]) != c.SHA256 {
			t.Errorf("case %d (%dx%d seed %d): %d bytes, want %d; digest differs", index, c.Width, c.Height, c.Seed, len(got), c.Length)
		}
		if bytes.Count(got, []byte("IDAT")) > 1 {
			multiChunk++
		}
	}
	if multiChunk < 5 {
		t.Fatalf("only %d images needed more than one IDAT chunk", multiChunk)
	}
}

// TestFilterChoice pins the line filter Pillow selects for a few lines whose
// answer is known by construction.
func TestFilterChoice(t *testing.T) {
	cases := []struct {
		name           string
		previous, line []byte
		want           byte
	}{
		{"black", []byte{9, 9, 9, 9, 9, 9}, []byte{0, 0, 0, 0, 0, 0}, 0},
		{"same as above", []byte{200, 10, 77, 3, 250, 90}, []byte{200, 10, 77, 3, 250, 90}, 2},
		{"solid", []byte{0, 0, 0, 0, 0, 0}, []byte{90, 90, 90, 90, 90, 90}, 1},
		{"small values stay unfiltered", []byte{0, 0, 0, 0, 0, 0}, []byte{1, 0, 0, 0, 0, 2}, 0},
		{"left then above", []byte{0, 0, 0, 0, 0, 0, 80, 80, 80}, []byte{50, 50, 50, 50, 50, 50, 80, 80, 80}, 4},
	}
	for _, c := range cases {
		e := newZipEncoder(len(c.line)/3, 1, nil)
		copy(e.previous[1:], c.previous)
		copy(e.buffer[1:], c.line)
		if got := e.filter()[0]; got != c.want {
			t.Errorf("%s: filter %d, want %d", c.name, got, c.want)
		}
	}
}
