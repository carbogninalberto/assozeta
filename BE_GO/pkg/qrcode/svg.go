// Package qrcode renders bounded, local QR symbols without provider or file I/O.
package qrcode

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"

	encoder "github.com/skip2/go-qrcode"
)

type Encoder struct{}

func New() *Encoder { return &Encoder{} }

func (*Encoder) SVG(ctx context.Context, content string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	// Provisioning URIs are percent-encoded ASCII containing lowercase text, so
	// Segno selects byte mode. Preserve its version/correction capacity even though
	// the Go encoder may split the actual payload into more efficient segments.
	// 2953 is the QR version-40-L byte capacity; reject before allocating a matrix.
	if len(content) == 0 || len(content) > 2953 {
		return "", errors.New("QR byte capacity exceeded")
	}
	probe := strings.Repeat("x", len(content))
	minimal, err := encoder.New(probe, encoder.Low)
	if err != nil {
		return "", errors.New("cannot size QR symbol")
	}
	level := encoder.Low
	for _, candidate := range []encoder.RecoveryLevel{encoder.Medium, encoder.High, encoder.Highest} {
		if _, err = encoder.NewWithForcedVersion(probe, minimal.VersionNumber, candidate); err != nil {
			break
		}
		level = candidate
	}
	if err = ctx.Err(); err != nil {
		return "", err
	}
	code, err := encoder.NewWithForcedVersion(content, minimal.VersionNumber, level)
	if err != nil {
		return "", errors.New("cannot encode QR symbol")
	}
	matrix := code.Bitmap()
	size := len(matrix)
	var xml strings.Builder
	fmt.Fprintf(&xml, "<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' class='segno'><path class='qrline' stroke='#000' d='", size, size)
	for y, row := range matrix {
		if err = ctx.Err(); err != nil {
			return "", err
		}
		for x := 0; x < len(row); {
			if !row[x] {
				x++
				continue
			}
			start := x
			for x < len(row) && row[x] {
				x++
			}
			fmt.Fprintf(&xml, "M%d %d.5h%d", start, y, x-start)
		}
	}
	xml.WriteString("'/></svg>")
	escaped := strings.ReplaceAll(url.QueryEscape(xml.String()), "+", "%20")
	return "data:image/svg+xml;charset=utf-8," + escaped, nil
}
