package dto

import "strconv"

// PythonBase64Error is the exception base64.b64decode raises, with its text.
type PythonBase64Error struct{ Message string }

func (e *PythonBase64Error) Error() string { return e.Message }

// PythonB64Decode is base64.b64decode(value) of a parsed JSON value (Python
// 3.13.16, validate=False): str must be ASCII; characters outside the
// alphabet are skipped; '=' after two or more data characters of a quad
// counts as padding, and a final partial quad needs enough padding after its
// last data character.
func PythonB64Decode(value any) ([]byte, error) {
	text, ok := value.(string)
	if !ok {
		return nil, &PythonBase64Error{Message: "argument should be a bytes-like object or ASCII string, not '" + pythonTypeName(value) + "'"}
	}
	for i := 0; i < len(text); i++ {
		if text[i] >= 0x80 {
			return nil, &PythonBase64Error{Message: "string argument should contain only ASCII characters"}
		}
	}
	out := make([]byte, 0, len(text)*3/4)
	var quad, pads int
	var left byte
	for i := 0; i < len(text); i++ {
		c := text[i]
		if c == '=' {
			if quad >= 2 {
				pads++
			}
			continue
		}
		v, valid := base64Value(c)
		if !valid {
			continue
		}
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, v
		case 1:
			out = append(out, left<<2|v>>4)
			quad, left = 2, v&0x0f
		case 2:
			out = append(out, left<<4|v>>2)
			quad, left = 3, v&0x03
		case 3:
			out = append(out, left<<6|v)
			quad, left = 0, 0
		}
	}
	switch {
	case quad == 1:
		return nil, &PythonBase64Error{Message: "Invalid base64-encoded string: number of data characters (" +
			strconv.Itoa(len(out)/3*4+1) + ") cannot be 1 more than a multiple of 4"}
	case quad != 0 && quad+pads < 4:
		return nil, &PythonBase64Error{Message: "Incorrect padding"}
	}
	return out, nil
}

func base64Value(c byte) (byte, bool) {
	switch {
	case c >= 'A' && c <= 'Z':
		return c - 'A', true
	case c >= 'a' && c <= 'z':
		return c - 'a' + 26, true
	case c >= '0' && c <= '9':
		return c - '0' + 52, true
	case c == '+':
		return 62, true
	case c == '/':
		return 63, true
	}
	return 0, false
}
