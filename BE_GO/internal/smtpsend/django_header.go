package smtpsend

// The header and address rendering of Django 5.2.18's EmailMessage:
// forbid_multi_line_headers and sanitize_address (django/core/mail/message.py)
// and the compat32 generator that writes the result
// (email/_policybase.py Compat32.fold_binary with refold_source 'long' and
// max_line_length 78, and email/generator.py BytesGenerator).

import (
	"errors"
	"fmt"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/idna2003"
)

// djangoMaxLineLength is compat32's max_line_length, the length Header.encode
// folds a header value to.
const djangoMaxLineLength = 78

// djangoAddressHeaders are the headers RFC 5322 defines as address lists.
var djangoAddressHeaders = map[string]bool{
	"from": true, "sender": true, "reply-to": true, "to": true, "cc": true, "bcc": true,
	"resent-from": true, "resent-sender": true, "resent-to": true, "resent-cc": true,
	"resent-bcc": true,
}

// djangoException is an exception Django raises while it builds the message
// (BadHeaderError, ValueError from sanitize_address, UnicodeEncodeError). It
// matches ErrRaises: the caller answers it the way Django's exception is
// answered.
func djangoException(kind, format string, args ...any) error {
	return fmt.Errorf("%w: %s: %s", ErrRaises, kind, fmt.Sprintf(format, args...))
}

// forbidMultiLineHeaders is django.core.mail.message.forbid_multi_line_headers
// with encoding 'utf-8': a value with a line break raises BadHeaderError, an
// address header is rebuilt from its sanitized addresses, any other value is
// RFC 2047 encoded (ASCII subjects are folded, ASCII values of other headers
// are kept).
func forbidMultiLineHeaders(name, value string) (string, error) {
	if strings.ContainsAny(value, "\r\n") {
		return "", djangoException("BadHeaderError", "header values can't contain newlines (got %q for header %q)", value, name)
	}
	if isASCII(value) {
		if strings.ToLower(name) == "subject" {
			return headerEncode([]headerChunk{{text: value, charset: charsetUSASCII}}, djangoMaxLineLength, "", " ")
		}
		return value, nil
	}
	if djangoAddressHeaders[strings.ToLower(name)] {
		addresses := getAddresses(value)
		parts := make([]string, 0, len(addresses))
		for _, address := range addresses {
			sanitized, err := sanitizeAddressPair(address.name, address.addr)
			if err != nil {
				return "", err
			}
			parts = append(parts, sanitized)
		}
		return strings.Join(parts, ", "), nil
	}
	return headerEncode([]headerChunk{{text: value, charset: charsetUTF8}}, djangoMaxLineLength, "", " ")
}

// djangoHeaderLine is one header as the BytesGenerator writes it: the value
// forbid_multi_line_headers produces, folded by compat32's header folding.
func djangoHeaderLine(name, value string) (string, error) {
	value, err := forbidMultiLineHeaders(name, value)
	if err != nil {
		return "", err
	}
	folded, err := foldDjangoHeader(name, value)
	if err != nil {
		return "", err
	}
	return name + ": " + folded + "\r\n", nil
}

// foldDjangoHeader is compat32's fold_binary of a header value:
// Header(value, header_name=name).encode(linesep="\r\n", maxlinelen=78).
func foldDjangoHeader(name, value string) (string, error) {
	runes := pythonRunes(value)
	if hasSurrogate(runes) {
		// compat32 writes a value with surrogates unchanged for cte_type
		// '8bit', but forbid_multi_line_headers re-encodes every value it
		// accepts, so a header Django renders never holds one.
		return "", djangoException("UnicodeEncodeError", "surrogates not allowed")
	}
	folded, err := foldHeaderLine(name, value, djangoMaxLineLength)
	if err != nil {
		return "", err
	}
	// Header.encode's linesep: the folds Header writes are CRLF for Django.
	return strings.ReplaceAll(folded, "\n", "\r\n"), nil
}

// sanitizeAddress is django.core.mail.message.sanitize_address(addr, 'utf-8')
// for a str address: the mailbox parse of the value, then the display name,
// local part and domain encoded as Django writes them.
func sanitizeAddress(address string) (string, error) {
	token, rest, err := getMailbox(address)
	if err != nil {
		if isHeaderParseError(err) || errors.Is(err, errHeaderIndex) {
			return "", djangoException("ValueError", "Invalid address %q", address)
		}
		return "", err
	}
	if rest != "" {
		return "", djangoException("ValueError", "Invalid address; only %s could be parsed from %q", token.str(), address)
	}
	name, err := mailboxDisplayName(token)
	if err != nil {
		if errors.Is(err, errHeaderIndex) {
			return "", djangoException("ValueError", "Invalid address %q", address)
		}
		return "", err
	}
	localPart, err := mailboxLocalPart(token)
	if err != nil {
		return "", err
	}
	domain, err := mailboxDomain(token)
	if err != nil {
		return "", err
	}
	display := ""
	if name != nil {
		display = *name
	}
	if domain == nil {
		// token.domain or "": a bare local part has no domain, not None.
		domain = ptrTo("")
	}
	return sanitizeAddressParts(display, localPart, domain)
}

// sanitizeAddressPair is sanitize_address for a (name, address) pair, which is
// what forbid_multi_line_headers passes for an address header.
func sanitizeAddressPair(name, address string) (string, error) {
	at := strings.LastIndexByte(address, '@')
	if at < 0 {
		return "", djangoException("ValueError", "Invalid address %q", address)
	}
	localPart, domain := address[:at], address[at+1:]
	return sanitizeAddressParts(name, &localPart, &domain)
}

// sanitizeAddressParts is the rest of sanitize_address: the parts check, the
// display name and local part encoded with Header, the domain through the idna
// codec, and formataddr of the headerregistry Address.
func sanitizeAddressParts(name string, localPart, domain *string) (string, error) {
	if localPart == nil || domain == nil {
		// nm + None is a TypeError, raised before the parts are checked.
		return "", djangoException("TypeError", "can only concatenate str (not \"NoneType\") to str")
	}
	if strings.ContainsAny(name+*localPart+*domain, "\r\n") {
		return "", djangoException("ValueError", "Invalid address; address parts cannot contain newlines.")
	}
	encodedName, err := addressHeaderValue(name)
	if err != nil {
		return "", err
	}
	local := *localPart
	if !isASCII(local) {
		if local, err = headerEncode([]headerChunk{{text: local, charset: charsetUTF8}}, djangoMaxLineLength, "", " "); err != nil {
			return "", err
		}
	}
	punycoded, err := idna2003.ToASCII(*domain)
	if err != nil {
		// punycode() is domain.encode('idna'): the codec's UnicodeError.
		return "", djangoException("UnicodeError", "idna codec: %s", err)
	}
	if strings.ContainsAny(local+punycoded, "\r\n") {
		// email.headerregistry.Address(username=..., domain=...): a Header
		// fold can put a line break in an encoded local part or display name.
		return "", djangoException("ValueError", "invalid arguments; address parts cannot contain CR or LF")
	}
	return FormatAddr(encodedName, authorityAddrSpec(local, punycoded))
}

// addressHeaderValue is the display name as sanitize_address encodes it:
// Header(nm).encode() for ASCII and Header(nm, 'utf-8').encode() otherwise.
func addressHeaderValue(name string) (string, error) {
	if !isASCII(name) {
		return headerEncode([]headerChunk{{text: name, charset: charsetUTF8}}, djangoMaxLineLength, "", " ")
	}
	return headerEncode([]headerChunk{{text: name, charset: charsetUSASCII}}, djangoMaxLineLength, "", " ")
}

// authorityAddrSpec is email.headerregistry.Address(username=local,
// domain=domain).addr_spec: the local part is quoted when it holds a
// dot-atom end.
func authorityAddrSpec(local, domain string) string {
	if strings.ContainsAny(local, dotAtomEnds) {
		local = quoteString(local)
	}
	switch {
	case domain != "":
		return local + "@" + domain
	case local == "":
		return "<>"
	}
	return local
}

// getAddresses is email.utils.getaddresses((value,)) with its default strict
// validation: a value with unbalanced parentheses or a domain literal in a
// result is replaced by (”, ”), and a result count that differs from the
// number of separators yields a single (”, ”).
func getAddresses(value string) []addrPair {
	field := value
	if !checkParenthesis(field) {
		field = "('', '')"
	}
	parsed := (&addrParser{field: field}).getaddrlist()
	result := make([]addrPair, 0, len(parsed))
	for _, pair := range parsed {
		if strings.Contains(pair.addr, "[") {
			pair = addrPair{}
		}
		result = append(result, pair)
	}
	if len(result) != 1+strings.Count(stripQuotedRealnames(field), ",") {
		return []addrPair{{}}
	}
	return result
}
