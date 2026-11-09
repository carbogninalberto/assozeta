package repository

import (
	"context"
	"encoding/binary"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// medicalUploadTTL is application.impersonation.SESSION_TTL.
const medicalUploadTTL = 3600 * time.Second

// RememberMedicalUpload is remember_medical_upload: cache.set(medical_upload_key(request,
// id), str(association.pk), SESSION_TTL) in Django's Redis cache (version 1,
// RedisSerializer: pickle protocol 5). The session is the raw request header.
func (r *DjangoImpersonationCache) RememberMedicalUpload(ctx context.Context, session, certificate, association string) error {
	if _, err := uuid.Parse(association); err != nil || len(session) > 128 || len(certificate) > 128 {
		return domain.ErrInvalidScopedRequest
	}
	return r.client.Set(ctx, ":1:impersonation-upload:"+session+":"+certificate, pickleShortString(association), medicalUploadTTL).Err()
}

// pickleShortString is pickle.dumps(text, 5) for UTF-8 text under 256 bytes:
// PROTO, FRAME, SHORT_BINUNICODE, MEMOIZE, STOP.
func pickleShortString(text string) []byte {
	payload := append([]byte{0x8c, byte(len(text))}, text...)
	payload = append(payload, 0x94, '.')
	out := []byte{0x80, 5, 0x95}
	out = binary.LittleEndian.AppendUint64(out, uint64(len(payload)))
	return append(out, payload...)
}
