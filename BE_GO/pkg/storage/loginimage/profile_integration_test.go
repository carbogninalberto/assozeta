//go:build imageintegration

package loginimage

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"sync"
	"testing"
)

func TestProfileImageSharedAdmissionAcrossModes(t *testing.T) {
	testImageSharedAdmission(t, true)
}

func TestProfileImageAgainstDjango(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_PROFILE_IMAGE_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, URI, Expected string
		Gap                 bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) != 52 {
		t.Fatal("required upload fixture", len(cases), err)
	}
	p, err := NewProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), imageProcessorTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	check := func(i int) {
		t.Helper()
		c := cases[i]
		got, err := p.CompressProfileImage(context.Background(), c.URI)
		if c.Gap {
			if got != "" || !errors.Is(err, ErrImageDecoderUnavailable) {
				t.Errorf("%s: missing codec silently accepted: %v", c.Name, err)
			}
		} else if err != nil || got != c.Expected {
			t.Errorf("%s: output mismatch, err=%v", c.Name, err)
		}
	}
	for i := range cases {
		check(i)
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); check(i) }()
	}
	wg.Wait()
	if len(p.slots) != 0 {
		t.Fatal("profile upload leaked shared admission")
	}
}
