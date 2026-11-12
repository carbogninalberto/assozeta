package service

import (
	"context"
	"io"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

type TwoFactorSetup struct {
	random       io.Reader
	qr           domain.QRCodeEncoder
	name, issuer string
}

func NewTwoFactorSetup(random io.Reader, qr domain.QRCodeEncoder, name, issuer string) *TwoFactorSetup {
	return &TwoFactorSetup{random: random, qr: qr, name: name, issuer: issuer}
}
func (s *TwoFactorSetup) Setup(ctx context.Context, identity domain.ResolvedIdentity) (domain.TwoFactorProvisioning, error) {
	empty := domain.TwoFactorProvisioning{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.ScopeUser.ID == "" {
		return empty, domain.ErrInvalidScopedRequest
	}
	secret, err := passwordcrypto.GenerateTOTPSecret(s.random)
	if err != nil {
		return empty, err
	}
	if err = ctx.Err(); err != nil {
		return empty, err
	}
	uri := passwordcrypto.TOTPProvisioningURI(secret, s.name, s.issuer)
	svg, err := s.qr.SVG(ctx, uri)
	if err != nil {
		return empty, err
	}
	if err = ctx.Err(); err != nil {
		return empty, err
	}
	return domain.TwoFactorProvisioning{Secret: secret, QRCodeURI: svg}, nil
}
