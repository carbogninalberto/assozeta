package domain

import "context"

type TwoFactorProvisioning struct {
	Secret    string `json:"-"`
	QRCodeURI string `json:"-"`
}

type QRCodeEncoder interface {
	SVG(context.Context, string) (string, error)
}
