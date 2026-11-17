package smtpsend

// The decoders CPython's bytes.decode runs for the RFC 2047 charsets beyond
// UTF-8, US-ASCII and ISO-8859-1 come from internal/pycodecs, which ports
// CPython 3.13's text codecs with their exact error spans and handlers.
//
// email._encoded_words.decode decodes an encoded word's bytes twice: the
// strict str(bstring, charset) first, and then, when that raises
// UnicodeDecodeError, str(bstring, charset, 'surrogateescape'). Any exception
// of the retry (a span starting below 0x80 re-raises, and the strict-only
// codecs raise UnicodeError for the handler), and any other UnicodeError of
// the strict decode, is a ValueError leaving _encoded_words.decode, so
// get_encoded_word raises _InvalidEwError, an email.errors.HeaderParseError:
// the encoded word is invalid, not unported, and the callers fall back
// exactly as they do for a malformed word.
//
// The strict-only codecs are idna and undefined. idna's strict decode
// (nameprep, ToUnicode) is carried by pycodecs, so an idna encoded word is
// decoded like any other charset; when the name in it is not a valid IDN the
// strict DecodeError makes the surrogateescape retry raise, which is the
// invalid-word fallback described above.

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pycodecs"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// decodePythonCodec is str(raw, name) with _encoded_words.decode's
// surrogateescape retry, for a codec named by codecs.lookup(...).name.
func decodePythonCodec(raw []byte, name string) (string, error) {
	if len(raw) == 0 {
		// PyUnicode_Decode returns "" for empty bytes before any codec runs.
		return "", nil
	}
	out, err, ok := pycodecs.Decode(name, raw, pycodecs.Strict)
	if !ok {
		// Every name decodeCharset passes comes from pythonTextCodecs, whose
		// canonical names pycodecs registers (TestCharsetCodecs); a miss is
		// this package's own registry going stale, not request data.
		return "", errCodecNotRegistered
	}
	if err == nil {
		return pystr.String(out), nil
	}
	var decodeErr *pycodecs.DecodeError
	if !errors.As(err, &decodeErr) {
		return "", errInvalidEncodedWord // UnicodeError: a ValueError
	}
	out, err, _ = pycodecs.Decode(name, raw, pycodecs.SurrogateEscape)
	if err != nil {
		return "", errInvalidEncodedWord
	}
	return pystr.String(out), nil
}

// errCodecNotRegistered is a codec name of pythonTextCodecs that pycodecs does
// not register: the package's own tables disagree (TestCharsetCodecs), never
// request data.
var errCodecNotRegistered = refusal.AppState("smtpsend: text codec not registered in pycodecs")
