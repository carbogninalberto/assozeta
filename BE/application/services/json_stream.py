"""Incremental decoding for the large JSON arrays stored in export archives."""
import codecs
import json

DEFAULT_CHUNK_SIZE = 1024 * 1024
_WHITESPACE = ' \t\r\n'
_VALUE_TERMINATORS = frozenset(' \t\r\n,]')


class _ChunkedTextReader:
    """Decode a binary stream incrementally, distinguishing EOF from buffering."""

    def __init__(self, stream, chunk_size):
        self.stream = stream
        self.chunk_size = chunk_size
        self.decoder = None
        self.pending = b''
        self.eof = False

    def _start_decoder(self, final=False):
        # json.detect_encoding mirrors json.loads support for UTF-8/16/32,
        # with or without a BOM.  Four bytes are enough to detect all of them,
        # and json.loads decodes bytes with the same surrogatepass policy.
        self.decoder = codecs.getincrementaldecoder(
            json.detect_encoding(self.pending)
        )(errors='surrogatepass')
        text = self.decoder.decode(self.pending, final)
        self.pending = b''
        return text

    def read(self):
        """Return the next decoded text; empty while a partial character waits."""
        if self.eof:
            return ''
        raw = self.stream.read(self.chunk_size)
        if not raw:
            self.eof = True
            if self.decoder is None:
                return self._start_decoder(final=True) if self.pending else ''
            return self.decoder.decode(b'', final=True)
        if self.decoder is None:
            self.pending += raw
            if len(self.pending) < 4:
                return ''
            return self._start_decoder()
        return self.decoder.decode(raw)


class _JsonBuffer:
    """Sliding window over the decoded stream with an index-based cursor."""

    def __init__(self, reader):
        self.reader = reader
        self.data = ''
        self.pos = 0

    def fill(self):
        """Drop the consumed prefix, then append the next decoded chunk."""
        if self.pos:
            self.data = self.data[self.pos:]
            self.pos = 0
        while True:
            chunk = self.reader.read()
            if chunk:
                self.data += chunk
                return True
            if self.reader.eof:
                return False

    def peek(self):
        """Return the next non-whitespace character, or '' at end of input."""
        while True:
            while self.pos < len(self.data):
                char = self.data[self.pos]
                if char in _WHITESPACE:
                    self.pos += 1
                    continue
                return char
            if not self.fill():
                return ''

    def expect_end(self):
        """Consume the remaining input, which must contain only whitespace."""
        while True:
            while self.pos < len(self.data):
                if self.data[self.pos] not in _WHITESPACE:
                    raise ValueError('Extra data after JSON array')
                self.pos += 1
            if not self.fill():
                return


def iter_json_array(stream, chunk_size=DEFAULT_CHUNK_SIZE):
    """Yield the items of a top-level JSON array one at a time.

    ``stream`` must be a readable binary file object, such as the member
    returned by ``zipfile.ZipFile.open``.  Items are decoded with the standard
    library decoder as data arrives, so peak memory is bounded by the read
    buffer plus the largest single item instead of the entire file: a backup
    no longer has to fit in RAM as encoded bytes and parsed objects at once.

    The accepted input is the same as ``json.loads`` for an array: an array of
    JSON values, optional surrounding whitespace, no trailing content, and the
    same UTF-8/16/32 encodings.
    """
    decoder = json.JSONDecoder()
    buffer = _JsonBuffer(_ChunkedTextReader(stream, chunk_size))
    # first -> '['; value_or_end -> first item or ']'; separator -> ',' or ']';
    # value -> item required after a comma.
    state = 'first'

    while True:
        char = buffer.peek()
        if not char:
            raise ValueError(
                'Expected a JSON array' if state == 'first' else 'Unterminated JSON array'
            )

        if state == 'first':
            if char != '[':
                raise ValueError('Expected a JSON array')
            state = 'value_or_end'
            buffer.pos += 1
            continue

        if char == ']':
            if state == 'value':
                raise ValueError('Expecting value')
            buffer.pos += 1
            buffer.expect_end()
            return

        if char == ',':
            if state != 'separator':
                raise ValueError('Expecting value')
            state = 'value'
            buffer.pos += 1
            continue

        if state == 'separator':
            raise ValueError("Expecting ',' delimiter")

        while True:
            try:
                item, end = decoder.raw_decode(buffer.data, buffer.pos)
            except ValueError:
                if not buffer.fill():
                    raise
                continue
            # A chunk boundary can truncate a number or literal into a shorter
            # valid value (for example "1.5e3" decoded as 1).  Only accept a
            # decode once a terminator character or the end of the stream
            # proves the token is complete.
            terminator = buffer.data[end] if end < len(buffer.data) else None
            if terminator in _VALUE_TERMINATORS:
                buffer.pos = end
                break
            if terminator is None and buffer.reader.eof:
                buffer.pos = end
                break
            if buffer.reader.eof:
                raise ValueError("Expecting ',' delimiter")
            buffer.fill()
        yield item
        state = 'separator'
