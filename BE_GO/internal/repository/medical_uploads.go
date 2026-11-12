package repository

import (
	"context"
	"encoding/binary"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// MedicalUploads reads the exact raw-header/raw-reference namespace used by
// remember_medical_upload. Unlike session lookup, this key is not canonicalized.
// MGET never extends TTL; the session itself must already have been revalidated.
func (r *DjangoImpersonationCache) MedicalUploads(ctx context.Context, session string, ids []string) (map[string]string, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if _, err := uuid.Parse(session); err != nil || len(session) > 128 || len(ids) > MaxResourceBatch {
		return nil, domain.ErrInvalidScopedRequest
	}
	keys := make([]string, len(ids))
	for i, id := range ids {
		if len(id) == 0 || len(id) > 128 {
			return nil, domain.ErrInvalidScopedRequest
		}
		keys[i] = ":1:impersonation-upload:" + session + ":" + id
	}
	result := map[string]string{}
	if len(ids) == 0 {
		return result, nil
	}
	values, err := r.client.MGet(ctx, keys...).Result()
	if err != nil {
		return nil, err
	}
	for i, value := range values {
		if value == nil {
			continue
		}
		raw, ok := value.(string)
		if !ok {
			return nil, domain.ErrInvalidScopedRequest
		}
		association, err := decodeMedicalUpload([]byte(raw))
		if err != nil {
			return nil, err
		}
		result[ids[i]] = association
	}
	return result, nil
}

// Closed protocol-4/5 UUID-string parser. Never executes pickle constructors.
func decodeMedicalUpload(data []byte) (string, error) {
	if len(data) < 5 || len(data) > 128 || data[0] != 0x80 || (data[1] != 4 && data[1] != 5) {
		return "", domain.ErrInvalidScopedRequest
	}
	p := sessionPickle{data: data, pos: 2}
	if p.take(0x95) {
		if p.pos+8 > len(data) {
			return "", domain.ErrInvalidScopedRequest
		}
		size := binary.LittleEndian.Uint64(data[p.pos : p.pos+8])
		p.pos += 8
		if size != uint64(len(data)-p.pos) {
			return "", domain.ErrInvalidScopedRequest
		}
	}
	value, ok := p.value()
	if !ok || !p.take('.') || p.pos != len(data) {
		return "", domain.ErrInvalidScopedRequest
	}
	association, ok := value.(string)
	if !ok {
		return "", nil
	}
	id, err := uuid.Parse(association)
	if err != nil || id.String() != association {
		return "", nil
	}
	return association, nil
}
