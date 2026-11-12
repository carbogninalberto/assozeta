package loginimage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestProfileImageSizePolicy(t *testing.T) {
	for _, tc := range []struct {
		w, h, bytes, wantW, wantH int
		keep, invalid             bool
	}{
		{512, 256, 1 << 20, 512, 256, true, false},
		{256, 512, 1, 256, 512, true, false},
		{200, 100, 1, 200, 100, false, false},
		{1024, 512, 1 << 20, 1024, 512, false, false},
		{1024, 512, 1<<20 + 1, 512, 256, false, false},
		{3, 2, 1<<20 + 1, 512, 341, false, false},
		{1, 1000000, 1 << 20, 1, 1000000, false, false},
		{1, 1000000, 1<<20 + 1, 0, 0, false, true},
	} {
		got, err := decideSize(tc.w, tc.h, tc.bytes, squareBound(512))
		if tc.invalid {
			if !errors.Is(err, ErrDimensions) {
				t.Fatalf("%+v: %v", tc, err)
			}
		} else if err != nil || got != (Plan{tc.wantW, tc.wantH, tc.keep}) {
			t.Fatalf("%+v: %+v, %v", tc, got, err)
		}
	}
}

func TestProfileImageLazyPolicyAndSharedAdmission(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codec")
	if err := os.WriteFile(path, []byte("removed executable"), 0700); err != nil {
		t.Fatal(err)
	}
	p, err := NewProcessor(path, imageProcessorTestLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	// Complete pixels distinguish a lazy passthrough from malformed-image fallback.
	value := imageURI([]byte("P5\n512 256\n255\n" + strings.Repeat("I", 512*256)))
	if got, err := p.CompressProfileImage(context.Background(), value); err != nil || got != value {
		t.Fatal("512-pixel upload should retain original without invoking codec", err)
	}
	if got, err := p.CompressLoginImage(context.Background(), value); got != "" || !errors.Is(err, ErrJPEGCodecUnavailable) {
		t.Fatal("200-pixel presentation should invoke codec", err)
	}
	for i := 0; i < cap(p.slots); i++ {
		p.slots <- struct{}{}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if got, err := p.CompressProfileImage(ctx, value); got != "" || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("profile upload bypassed shared admission", err)
	}
	for i := 0; i < cap(p.slots); i++ {
		<-p.slots
	}
	if got, err := p.CompressProfileImage(context.Background(), value); err != nil || got != value {
		t.Fatal("cancelled upload retained shared slot", err)
	}
	if got, err := p.CompressProfileImage(context.Background(), strings.Repeat("x", p.limits.MaxURIBytes+1)); got != "" || !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("profile upload bypassed URI budget", err)
	}
}

// The subscription-template logo bound is rectangular: each axis has its own limit.
func TestTemplateLogoSizePolicy(t *testing.T) {
	bound := sizeBound{590, 130}
	for _, tc := range []struct {
		w, h, bytes, wantW, wantH int
		keep, invalid             bool
	}{
		{590, 130, 1 << 20, 590, 130, true, false},
		{295, 130, 1, 295, 130, true, false},
		{590, 65, 1, 590, 65, true, false},
		{590, 131, 1, 590, 131, false, false},
		{1180, 260, 1<<20 + 1, 590, 130, false, false},
		{2000, 300, 1<<20 + 1, 590, 88, false, false},
		{300, 2000, 1<<20 + 1, 19, 130, false, false},
		{640, 141, 1<<20 + 1, 590, 129, false, false},
		{97, 31, 1<<20 + 1, 406, 130, false, false},
		{3, 1000, 1<<20 + 1, 0, 0, false, true},
	} {
		got, err := decideSize(tc.w, tc.h, tc.bytes, bound)
		if tc.invalid {
			if !errors.Is(err, ErrDimensions) {
				t.Fatalf("%+v: %v", tc, err)
			}
		} else if err != nil || got != (Plan{tc.wantW, tc.wantH, tc.keep}) {
			t.Fatalf("%+v: %+v, %v", tc, got, err)
		}
	}
	if _, err := decideSize(10, 10, 1, sizeBound{10, 0}); !errors.Is(err, ErrDimensions) {
		t.Fatal("non-positive bound accepted")
	}
}
