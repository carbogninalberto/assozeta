//go:build imageintegration

package loginimage

import (
	"context"
	"encoding/json"
	"os"
	"sync"
	"testing"
)

// TestTemplateLogoAgainstDjango compares the 590x130 bound with the actual
// compress_base64 output (TESTS/adapters/django/template_logo_images.py).
func TestTemplateLogoAgainstDjango(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_TEMPLATE_LOGO_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct{ Name, URI, Expected string }
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) != 50 {
		t.Fatal("required template logo fixture", len(cases), err)
	}
	p, err := NewProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), imageProcessorTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		c := cases[i]
		if got, err := p.CompressTemplateLogo(context.Background(), c.URI); err != nil || got != c.Expected {
			t.Errorf("%s: output mismatch, err=%v", c.Name, err)
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(i) }()
	}
	wg.Wait()
	if len(p.slots) != 0 {
		t.Fatal("template logo leaked shared admission")
	}
}
