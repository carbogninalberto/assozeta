package pyheader

// SingleByteTable returns the byte -> code point table of an 8-bit charmap
// text codec, keyed by the canonical name codecs.lookup(name).name ("cp1252",
// "iso8859-2", "koi8-r", ...), or nil when the codec is not one of them.
//
// The table is singleByteDecoders: str(bytes([b]), codec, errors='replace')
// over all 256 bytes, where U+FFFD marks a byte CPython leaves undefined
// (CPython's own decoding tables use the same marker, and no codec maps a
// byte to U+FFFD itself). A caller that needs str(b, codec) decodes defined
// bytes through the table and treats U+FFFD entries as the strict decoder's
// UnicodeDecodeError positions. The table is shared and must not be modified.
func SingleByteTable(name string) *[256]rune {
	return singleByteDecoders[name]
}
