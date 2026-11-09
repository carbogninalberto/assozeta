package service

import (
	"bytes"
	"compress/zlib"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"strings"
	"time"
	"unicode/utf8"
)

// djangoSignedValue is the outcome of django.core.signing.loads: payload with
// valid=true, valid=false for BadSignature/SignatureExpired, or an error for
// the exceptions loads lets escape (a 500 page in the calling view).
type djangoSignedValue struct {
	valid   bool
	payload any
}

var errSigningMalformed = errors.New("malformed signed value")

// djangoSigningLoads is signing.loads(token, salt=salt, max_age=maxAge) with
// the JSON serializer and SECRET_KEY as the only key: Signer.unsign (rsplit on
// ':', constant-time comparison of the base64 HMAC-SHA256 signature keyed by
// sha256(salt+"signer"+secret)), TimestampSigner.unsign (base62 timestamp, age
// strictly above maxAge expires), then unsign_object ('.' marks zlib, unpadded
// urlsafe base64, latin-1 JSON). JSON numbers are kept as json.Number.
func djangoSigningLoads(token, salt, secret string, maxAge float64, now time.Time) (djangoSignedValue, error) {
	cut := strings.LastIndexByte(token, ':')
	if cut < 0 {
		return djangoSignedValue{}, nil
	}
	value, signature := token[:cut], token[cut+1:]
	key := sha256.Sum256([]byte(salt + "signer" + secret))
	mac := hmac.New(sha256.New, key[:])
	mac.Write([]byte(value))
	expected := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	if !hmac.Equal([]byte(signature), []byte(expected)) {
		return djangoSignedValue{}, nil
	}
	cut = strings.LastIndexByte(value, ':')
	if cut < 0 {
		return djangoSignedValue{}, errSigningMalformed // tuple unpacking ValueError
	}
	segment, stamp := value[:cut], value[cut+1:]
	timestamp, err := base62Decode(stamp)
	if err != nil {
		return djangoSignedValue{}, err
	}
	stamped, accuracy := new(big.Float).SetInt(timestamp).Float64()
	if math.IsInf(stamped, 0) || accuracy != big.Exact && math.Abs(stamped) == math.MaxFloat64 {
		return djangoSignedValue{}, errSigningMalformed // time.time() - int: OverflowError
	}
	age := new(big.Float).Sub(new(big.Float).SetFloat64(float64(now.UnixNano())/1e9), new(big.Float).SetInt(timestamp))
	if age.Cmp(big.NewFloat(maxAge)) > 0 {
		return djangoSignedValue{}, nil
	}
	compressed := strings.HasPrefix(segment, ".")
	if compressed {
		segment = segment[1:]
	}
	data, err := pythonURLSafeB64Decode(segment)
	if err != nil {
		return djangoSignedValue{}, err
	}
	if compressed {
		reader, err := zlib.NewReader(bytes.NewReader(data))
		if err != nil {
			return djangoSignedValue{}, err
		}
		data, err = io.ReadAll(reader)
		if err != nil {
			return djangoSignedValue{}, err
		}
	}
	// JSONSerializer.loads decodes the bytes as latin-1.
	text := make([]rune, len(data))
	for i, b := range data {
		text[i] = rune(b)
	}
	decoder := json.NewDecoder(strings.NewReader(string(text)))
	decoder.UseNumber()
	var payload any
	if err := decoder.Decode(&payload); err != nil {
		return djangoSignedValue{}, err
	}
	if decoder.More() {
		return djangoSignedValue{}, errSigningMalformed // json "Extra data"
	}
	if _, err := decoder.Token(); err != io.EOF {
		return djangoSignedValue{}, errSigningMalformed
	}
	return djangoSignedValue{valid: true, payload: payload}, nil
}

// base62Decode is django.utils.baseconv-free signing.b62_decode.
func base62Decode(text string) (*big.Int, error) {
	if text == "0" {
		return big.NewInt(0), nil
	}
	if text == "" {
		return nil, errSigningMalformed // s[0] IndexError
	}
	negative := text[0] == '-'
	if negative {
		text = text[1:]
	}
	decoded := new(big.Int)
	for _, digit := range text {
		index := strings.IndexRune(base62Alphabet, digit)
		if index < 0 || digit >= utf8.RuneSelf {
			return nil, errSigningMalformed // str.index ValueError
		}
		decoded.Mul(decoded, big.NewInt(62)).Add(decoded, big.NewInt(int64(index)))
	}
	if negative {
		decoded.Neg(decoded)
	}
	return decoded, nil
}

// pythonURLSafeB64Decode is signing.b64_decode: pad to a multiple of four and
// base64.urlsafe_b64decode, which maps '-'/'_' and then decodes like
// binascii.a2b_base64 in non-strict mode (other bytes are skipped, a complete
// pad sequence ends the input).
func pythonURLSafeB64Decode(segment string) ([]byte, error) {
	input := []byte(segment)
	input = append(input, bytes.Repeat([]byte("="), (4-len(input)%4)%4)...)
	var out []byte
	quad, pads := 0, 0
	var left byte
	for _, ch := range input {
		switch {
		case ch == '-':
			ch = '+'
		case ch == '_':
			ch = '/'
		}
		if ch == '=' {
			pads++
			if quad >= 2 && quad+pads >= 4 {
				return out, nil
			}
			continue
		}
		value := strings.IndexByte("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", ch)
		if value < 0 {
			continue
		}
		pads = 0
		v := byte(value)
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
	if quad != 0 {
		return nil, errSigningMalformed // binascii.Error
	}
	return out, nil
}
