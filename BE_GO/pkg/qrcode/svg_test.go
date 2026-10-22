package qrcode

import (
	"context"
	"errors"
	"net/url"
	"strings"
	"testing"
)

func TestSVGByteCapacityAndCancellation(t *testing.T) {
	value := "otpauth://totp/Bakney%20Sport:support%40example.test?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=Bakney%20Sport"
	svg, err := New().SVG(context.Background(), value)
	if err != nil {
		t.Fatal(err)
	}
	prefix := "data:image/svg+xml;charset=utf-8,"
	if !strings.HasPrefix(svg, prefix) {
		t.Fatal("not SVG data URI")
	}
	xml, err := url.PathUnescape(strings.TrimPrefix(svg, prefix))
	if err != nil || !strings.Contains(xml, "width='49' height='49'") || !strings.Contains(xml, "stroke='#000'") {
		t.Fatal("reference symbol dimensions or color differ")
	}
	if _, err = New().SVG(context.Background(), strings.Repeat("x", 2954)); err == nil {
		t.Fatal("oversized byte-mode QR accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = New().SVG(ctx, value); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled QR generated")
	}
}
