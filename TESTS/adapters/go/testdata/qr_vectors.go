// Emit synthetic deterministic symbols for the independent Python QR reader.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/base32"
	"encoding/json"
	"fmt"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/qrcode"
)

func main() {
	if len(os.Args) != 2 {
		panic("QR output path required")
	}
	type vector struct {
		URI string `json:"uri"`
		SVG string `json:"svg"`
	}
	vectors := make([]vector, 256)
	for i := range vectors {
		entropy := sha256.Sum256([]byte(fmt.Sprintf("synthetic-qr-%d", i)))
		secret := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(entropy[:20])
		uri := "otpauth://totp/Assozeta%20Contract:setup-support%40example.test?secret=" + secret + "&issuer=Assozeta%20Contract"
		svg, err := qrcode.New().SVG(context.Background(), uri)
		if err != nil {
			panic("QR fixture encoder failed")
		}
		vectors[i] = vector{uri, svg}
	}
	data, err := json.Marshal(vectors)
	if err != nil {
		panic(err)
	}
	if err = os.WriteFile(os.Args[1], data, 0600); err != nil {
		panic(err)
	}
}
