package loginimage

import (
	"context"
	"errors"
	"strings"
)

var ErrDataURI = errors.New("invalid login image data URI")

// DecodeDataURI mirrors Python b64decode(validate=False), after the first comma.
// The header is opaque. ASCII garbage and leading/excess padding are accepted;
// padding inside the data is ignored, and trailing padding completes a final
// group. Any non-ASCII payload is invalid (CPython 3.13.16).
// Invalid input never returns partially decoded bytes.
func DecodeDataURI(ctx context.Context, value string, maxDecodedBytes int) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if maxDecodedBytes <= 0 {
		return nil, ErrWorkingLimit
	}
	_, payload, found := strings.Cut(value, ",")
	if !found {
		return nil, ErrDataURI
	}
	for i := 0; i < len(payload); i++ {
		if i&4095 == 0 {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
		}
		if payload[i] > 127 {
			return nil, ErrDataURI
		}
	}
	output := make([]byte, 0, min(len(payload)/4*3, maxDecodedBytes))
	position, padding := 0, 0
	carry := byte(0)
	for i := 0; i < len(payload); i++ {
		if i&4095 == 0 {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
		}
		ch := payload[i]
		if ch == '=' {
			padding = min(padding+1, 4)
			continue
		}
		n := byte(0)
		switch {
		case ch >= 'A' && ch <= 'Z':
			n = ch - 'A'
		case ch >= 'a' && ch <= 'z':
			n = ch - 'a' + 26
		case ch >= '0' && ch <= '9':
			n = ch - '0' + 52
		case ch == '+':
			n = 62
		case ch == '/':
			n = 63
		default:
			continue
		}
		padding = 0
		if position != 0 && len(output) == maxDecodedBytes {
			return nil, ErrWorkingLimit
		}
		switch position {
		case 0:
			carry = n << 2
		case 1:
			output = append(output, carry|n>>4)
			carry = n << 4
		case 2:
			output = append(output, carry|n>>2)
			carry = n << 6
		case 3:
			output = append(output, carry|n)
		}
		position = (position + 1) % 4
	}
	if position == 1 || position != 0 && position+padding < 4 {
		return nil, ErrDataURI
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return output, nil
}
