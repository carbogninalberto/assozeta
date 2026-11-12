package repository

import (
	"bytes"
	"encoding/binary"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// This is a closed parser for the impersonation:v1 dictionary emitted by the
// configured django-redis serializer. It is NOT a general pickle implementation.
// Only protocols 4/5, a single small dictionary, strings, None and string memo
// references are accepted. Object construction, globals and calls are forbidden.
func decodeImpersonation(data []byte) (domain.ImpersonationSession, error) {
	fail := func() (domain.ImpersonationSession, error) {
		return domain.ImpersonationSession{}, domain.ErrInvalidImpersonation
	}
	if len(data) < 5 || len(data) > 1024 || data[0] != 0x80 || (data[1] != 4 && data[1] != 5) {
		return fail()
	}
	p := sessionPickle{data: data, pos: 2}
	if p.take(0x95) {
		if p.pos+8 > len(data) {
			return fail()
		}
		size := binary.LittleEndian.Uint64(data[p.pos : p.pos+8])
		p.pos += 8
		if size != uint64(len(data)-p.pos) {
			return fail()
		}
	}
	if !p.take('}') {
		return fail()
	}
	if p.take(0x94) {
		p.memo = append(p.memo, struct{}{})
	} // dictionary cannot be a string value
	if !p.take('(') {
		return fail()
	}
	fields := map[string]any{}
	for !p.take('u') {
		if len(fields) >= 3 {
			return fail()
		}
		key, ok := p.value()
		if !ok {
			return fail()
		}
		name, ok := key.(string)
		if !ok {
			return fail()
		}
		if _, exists := fields[name]; exists {
			return fail()
		}
		if name != "actor_id" && name != "target_id" && name != "association_id" {
			return fail()
		}
		value, ok := p.value()
		if !ok {
			return fail()
		}
		fields[name] = value
	}
	if !p.take('.') || p.pos != len(data) {
		return fail()
	}
	actor, ok := fields["actor_id"].(string)
	if !ok {
		return fail()
	}
	target, ok := fields["target_id"].(string)
	if !ok {
		return fail()
	}
	association := ""
	if value := fields["association_id"]; value != nil {
		association, ok = value.(string)
		if !ok {
			return fail()
		}
	}
	result := domain.ImpersonationSession{ActorID: actor, TargetID: target, AssociationID: association}
	if !validSessionRecord(result) {
		return fail()
	}
	return result, nil
}

type sessionPickle struct {
	data []byte
	pos  int
	memo []any
}

func (p *sessionPickle) take(op byte) bool {
	if p.pos < len(p.data) && p.data[p.pos] == op {
		p.pos++
		return true
	}
	return false
}
func (p *sessionPickle) value() (any, bool) {
	if p.pos >= len(p.data) {
		return nil, false
	}
	op := p.data[p.pos]
	p.pos++
	var value any
	switch op {
	case 'N':
		value = nil
	case 0x8c, 'X', 0x8d:
		width := 1
		if op == 'X' {
			width = 4
		}
		if op == 0x8d {
			width = 8
		}
		if p.pos+width > len(p.data) {
			return nil, false
		}
		var size uint64
		switch width {
		case 1:
			size = uint64(p.data[p.pos])
		case 4:
			size = uint64(binary.LittleEndian.Uint32(p.data[p.pos : p.pos+4]))
		case 8:
			size = binary.LittleEndian.Uint64(p.data[p.pos : p.pos+8])
		}
		p.pos += width
		if size > uint64(len(p.data)-p.pos) {
			return nil, false
		}
		raw := p.data[p.pos : p.pos+int(size)]
		p.pos += int(size)
		if !utf8.Valid(raw) {
			return nil, false
		}
		value = string(raw)
	case 'h', 'j':
		width := 1
		if op == 'j' {
			width = 4
		}
		if p.pos+width > len(p.data) {
			return nil, false
		}
		index := uint64(p.data[p.pos])
		if width == 4 {
			index = uint64(binary.LittleEndian.Uint32(p.data[p.pos : p.pos+4]))
		}
		p.pos += width
		if index >= uint64(len(p.memo)) {
			return nil, false
		}
		value = p.memo[index]
	default:
		return nil, false
	}
	if p.take(0x94) {
		p.memo = append(p.memo, value)
	}
	return value, true
}
func validSessionRecord(record domain.ImpersonationSession) bool {
	for _, id := range []string{record.ActorID, record.TargetID} {
		if parsed, err := uuid.Parse(id); err != nil || parsed.String() != id {
			return false
		}
	}
	if record.AssociationID != "" {
		if parsed, err := uuid.Parse(record.AssociationID); err != nil || parsed.String() != record.AssociationID {
			return false
		}
	}
	return true
}
func encodeImpersonation(record domain.ImpersonationSession) ([]byte, error) {
	if !validSessionRecord(record) {
		return nil, domain.ErrInvalidImpersonation
	}
	// A protocol-4 dictionary without memoization is readable by Python's actual
	// serializer. Include one frame, matching the small-record boundary above.
	var body bytes.Buffer
	body.WriteByte('}')
	body.WriteByte('(')
	writeString := func(value string) { body.WriteByte(0x8c); body.WriteByte(byte(len(value))); body.WriteString(value) }
	writeString("actor_id")
	writeString(record.ActorID)
	writeString("target_id")
	writeString(record.TargetID)
	writeString("association_id")
	if record.AssociationID == "" {
		body.WriteByte('N')
	} else {
		writeString(record.AssociationID)
	}
	body.WriteByte('u')
	body.WriteByte('.')
	encoded := make([]byte, 11, 11+body.Len())
	encoded[0] = 0x80
	encoded[1] = 4
	encoded[2] = 0x95
	binary.LittleEndian.PutUint64(encoded[3:], uint64(body.Len()))
	return append(encoded, body.Bytes()...), nil
}
