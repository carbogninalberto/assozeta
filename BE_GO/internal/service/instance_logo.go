package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type InstanceLogo struct{ storage domain.InstanceLogoStorage }

func NewInstanceLogo(storage domain.InstanceLogoStorage) *InstanceLogo {
	return &InstanceLogo{storage: storage}
}

func (s *InstanceLogo) Read(ctx context.Context) (*domain.InstanceLogo, error) {
	for _, candidate := range []struct{ extension, contentType string }{{"png", "image/png"}, {"jpg", "image/jpeg"}, {"svg", "image/svg+xml"}, {"webp", "image/webp"}} {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		filename := "logo." + candidate.extension
		exists, err := s.storage.Exists(ctx, "instance/"+filename)
		// Django checks existence outside its fallback exception boundary.
		if err != nil {
			return nil, err
		}
		if !exists {
			continue
		}
		file, err := s.storage.Open(ctx, "instance/"+filename)
		if err != nil {
			if file.Body != nil {
				file.Body.Close()
			}
			if canceled := ctx.Err(); canceled != nil {
				return nil, canceled
			}
			continue
		}
		if err := ctx.Err(); err != nil {
			if file.Body != nil {
				file.Body.Close()
			}
			return nil, err
		}
		return &domain.InstanceLogo{InstanceLogoFile: file, ContentType: candidate.contentType, Filename: filename}, nil
	}
	return nil, domain.ErrInstanceLogoMissing
}
