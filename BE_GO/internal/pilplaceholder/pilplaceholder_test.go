package pilplaceholder

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

type renderCase struct {
	Text   string `json:"text"`
	Error  string `json:"error"`
	SHA256 string `json:"sha256"`
	Length int    `json:"length"`
	PNG    string `json:"png"`
}

func label(text string) string {
	if len(text) > 60 {
		return strings.ToValidUTF8(text[:60], "") + "..."
	}
	return text
}

// TestRenderMatchesDjango requires the exact bytes Django produced for every
// text in testdata/render_golden_<zlib>.json.gz
// (TESTS/adapters/go/pil_placeholder_golden.py), once per zlib Pillow can be
// running on. Where the corpus carries the PNG, the decoded pixels are
// compared first, so a failure says whether the drawing or only the encoding
// differs.
func TestRenderMatchesDjango(t *testing.T) {
	for _, v := range variants {
		t.Run(v.name, func(t *testing.T) { testRender(t, "render_golden_"+v.name+".json.gz", v.zlib) })
	}
}

func testRender(t *testing.T, file string, z Zlib) {
	var golden struct {
		Cases []renderCase `json:"cases"`
	}
	readGolden(t, file, &golden)
	if len(golden.Cases) < 1000 {
		t.Fatalf("only %d cases", len(golden.Cases))
	}
	withPNG, raised := 0, 0
	for index, c := range golden.Cases {
		got, err := RenderWith(c.Text, z)
		if c.Error != "" {
			raised++
			if !errors.Is(err, ErrUnsupported) {
				t.Errorf("case %d: Pillow raises %s, Render returned %v", index, c.Error, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("case %d %q: %v", index, label(c.Text), err)
			continue
		}
		if c.PNG != "" {
			withPNG++
			want, err := base64.StdEncoding.DecodeString(c.PNG)
			if err != nil {
				t.Fatal(err)
			}
			wantW, wantH, wantPixels := decodeRGB(t, want)
			gotW, gotH, gotPixels := decodeRGB(t, got)
			if gotW != wantW || gotH != wantH {
				t.Errorf("case %d %q: size %dx%d, want %dx%d", index, label(c.Text), gotW, gotH, wantW, wantH)
				continue
			}
			if !bytes.Equal(gotPixels, wantPixels) {
				at := 0
				for gotPixels[at] == wantPixels[at] {
					at++
				}
				t.Errorf("case %d %q: pixel (%d,%d) is %d, want %d", index, label(c.Text), at/3%wantW, at/3/wantW, gotPixels[at], wantPixels[at])
				continue
			}
			if !bytes.Equal(got, want) {
				t.Errorf("case %d %q: pixels match but the PNG bytes differ", index, label(c.Text))
				continue
			}
		}
		sum := sha256.Sum256(got)
		if len(got) != c.Length || hex.EncodeToString(sum[:]) != c.SHA256 {
			t.Errorf("case %d %q: %d bytes, want %d; digest differs", index, label(c.Text), len(got), c.Length)
		}
	}
	if withPNG < 100 || raised < 2 {
		t.Fatalf("corpus too thin: %d with PNG, %d raising", withPNG, raised)
	}
}

func TestRenderRefusals(t *testing.T) {
	for _, text := range []string{"\xff", "ok\n\xc3", "\xed\xa0\x80"} {
		for _, v := range variants {
			_, err := RenderWith(text, v.zlib)
			if !errors.Is(err, ErrUnsupported) || !errors.Is(err, refusal.ErrNotPorted) {
				t.Errorf("%q: %v", text, err)
			}
		}
	}
	if _, err := RenderWith("a", Zlib(7)); !errors.Is(err, ErrUnsupported) {
		t.Errorf("unknown zlib: %v", err)
	}
}

// TestRenderShape pins the facts about the Django image that are easy to get
// wrong: the crop is taller than the canvas and the rest is black, and a
// character the font lacks is the .notdef box, not a blank.
func TestRenderShape(t *testing.T) {
	data, err := Render("a\n\n\n\n\n\n\n\n\nb")
	if err != nil {
		t.Fatal(err)
	}
	width, height, pixels := decodeRGB(t, data)
	if height != 5+15*10+10 {
		t.Fatalf("height %d", height)
	}
	if pixels[0] != 255 || pixels[(99*width)*3] != 255 || pixels[(100*width)*3] != 0 || pixels[len(pixels)-1] != 0 {
		t.Fatal("canvas rows must be white and the rows below row 100 black")
	}
	blank, _ := Render(" ")
	box, _ := Render("è")
	if bytes.Equal(blank, box) {
		t.Fatal("an unmapped character must draw the .notdef box")
	}
	if a, _ := Render("è"); !bytes.Equal(a, box) {
		t.Fatal("Render is not deterministic")
	}
	if a, _ := Render("山"); !bytes.Equal(a, box) {
		t.Fatal("every unmapped character is the same glyph")
	}
	classic, err := RenderWith("è", ZlibClassic)
	if err != nil {
		t.Fatal(err)
	}
	w1, h1, p1 := decodeRGB(t, box)
	w2, h2, p2 := decodeRGB(t, classic)
	if w1 != w2 || h1 != h2 || !bytes.Equal(p1, p2) {
		t.Fatal("the zlib choice must not change the pixels")
	}
}
