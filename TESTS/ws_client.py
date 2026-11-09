"""Small independent RFC 6455 client for WebSocket contract sessions.

It writes the opening request byte for byte, so malformed handshakes can be
scripted, and reports every server frame as a logical event: text/binary
payload (inflated when permessage-deflate is negotiated), pong, close code and
reason, or the TCP close. Client frames are masked unless a script asks
otherwise. No retry, no automatic reply except an optional close echo.
"""
import base64
import hashlib
import os
import select
import socket
import struct
import time
import zlib

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
DEFAULT_KEY = base64.b64encode(b'assozeta-ws-test').decode()


def accept_value(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


class Rejected(Exception):
    pass


class Handshake:
    def __init__(self, status, reason, headers, body, raw):
        self.status, self.reason, self.headers, self.body, self.raw = status, reason, headers, body, raw

    def header(self, name):
        values = [value for key, value in self.headers if key.lower() == name.lower()]
        return values[-1] if values else None


class Session:
    """One TCP connection. ``open`` sends the request and reads the response head."""

    def __init__(self, host, port, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buffer = b''
        self.eof = False
        self.inflater = None
        self.client_deflate = None
        self.timeout = timeout

    # -- raw IO --------------------------------------------------------------
    def _fill(self, deadline):
        if self.eof:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('websocket read deadline exceeded')
        ready, _, _ = select.select([self.sock], [], [], remaining)
        if not ready:
            raise TimeoutError('websocket read deadline exceeded')
        try:
            data = self.sock.recv(65536)
        except ConnectionResetError:
            data = b''
        if not data:
            self.eof = True
            return False
        self.buffer += data
        return True

    def _take(self, size, deadline):
        while len(self.buffer) < size:
            if not self._fill(deadline):
                return None
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def send_raw(self, data):
        self.sock.sendall(data)

    # -- handshake -----------------------------------------------------------
    def open(self, request_bytes, timeout=None):
        """Send the request; return a Handshake. A non-101 response reads to EOF."""
        deadline = time.monotonic() + (timeout or self.timeout)
        self.send_raw(request_bytes)
        while b'\r\n\r\n' not in self.buffer:
            if not self._fill(deadline):
                break
        head, separator, rest = self.buffer.partition(b'\r\n\r\n')
        if not separator:
            raw, self.buffer = self.buffer, b''
            return Handshake(None, None, [], b'', raw)
        lines = head.decode('latin-1').split('\r\n')
        version, status, reason = (lines[0].split(' ', 2) + ['', ''])[:3]
        headers = []
        for line in lines[1:]:
            name, _, value = line.partition(':')
            headers.append((name.strip(), value.strip()))
        self.buffer = rest
        if status != '101':
            # Read everything the server sends until it closes the connection.
            try:
                while self._fill(deadline):
                    pass
            except TimeoutError:
                pass
            body, self.buffer = self.buffer, b''
            return Handshake(int(status), reason, headers, body, head + separator + body)
        handshake = Handshake(101, reason, headers, b'', head + separator)
        extensions = handshake.header('Sec-WebSocket-Extensions')
        if extensions and extensions.split(';')[0].strip() == 'permessage-deflate':
            params = [item.strip() for item in extensions.split(';')[1:]]
            bits = 15
            for item in params:
                if item.startswith('server_max_window_bits='):
                    bits = int(item.split('=', 1)[1])
            self.inflater = {'bits': bits, 'reset': 'server_no_context_takeover' in params,
                             'object': zlib.decompressobj(-bits)}
            self.client_deflate = {'reset': 'client_no_context_takeover' in params,
                                   'object': zlib.compressobj(wbits=-15)}
        return handshake

    # -- frames ----------------------------------------------------------------
    def frame(self, opcode, payload=b'', *, fin=True, rsv=0, mask=True, mask_key=b'\x37\xfa\x21\x3d', length=None):
        first = (0x80 if fin else 0) | (rsv << 4) | opcode
        size = len(payload) if length is None else length
        header = bytes([first])
        bit = 0x80 if mask else 0
        if size < 126:
            header += bytes([bit | size])
        elif size < 1 << 16:
            header += bytes([bit | 126]) + struct.pack('!H', size)
        else:
            header += bytes([bit | 127]) + struct.pack('!Q', size)
        if mask:
            header += mask_key
            payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        self.send_raw(header + payload)

    def send_text(self, text, *, compress=False):
        data = text.encode('utf-8', 'surrogatepass') if isinstance(text, str) else text
        self._send_data(0x1, data, compress)

    def send_binary(self, data, *, compress=False):
        self._send_data(0x2, data, compress)

    def _send_data(self, opcode, data, compress):
        if compress:
            deflate = self.client_deflate
            compressed = deflate['object'].compress(data) + deflate['object'].flush(zlib.Z_SYNC_FLUSH)
            if compressed.endswith(b'\x00\x00\xff\xff'):
                compressed = compressed[:-4]
            if deflate['reset']:
                deflate['object'] = zlib.compressobj(wbits=-15)
            self.frame(opcode, compressed, rsv=4)
        else:
            self.frame(opcode, data)

    def send_close(self, code=None, reason=b'', raw=None):
        if raw is None:
            raw = b'' if code is None else struct.pack('!H', code) + (reason.encode() if isinstance(reason, str) else reason)
        self.frame(0x8, raw)

    def recv(self, timeout=None):
        """Return the next logical event dict; message fragments are joined."""
        deadline = time.monotonic() + (timeout or self.timeout)
        fragments, message_opcode, compressed = [], None, False
        while True:
            head = self._take(2, deadline)
            if head is None:
                return {'eof': True, 'pending': self.buffer.hex()} if self.buffer else {'eof': True}
            fin, rsv, opcode = head[0] >> 7, (head[0] >> 4) & 7, head[0] & 0xf
            masked, size = head[1] >> 7, head[1] & 0x7f
            if size == 126:
                size = struct.unpack('!H', self._take(2, deadline))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._take(8, deadline))[0]
            key = self._take(4, deadline) if masked else None
            payload = self._take(size, deadline) if size else b''
            if payload is None:
                return {'eof': True, 'truncated_frame': opcode}
            if key:
                payload = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                code = struct.unpack('!H', payload[:2])[0] if len(payload) >= 2 else None
                event = {'close': code, 'reason': payload[2:].decode('utf-8', 'replace')}
                if masked:
                    event['masked'] = True
                return event
            if opcode == 0x9:
                return {'ping': payload.hex()}
            if opcode == 0xA:
                return {'pong': payload.hex()}
            if opcode in (0x1, 0x2):
                message_opcode, compressed = opcode, bool(rsv & 4)
            fragments.append(payload)
            if not fin:
                continue
            data = b''.join(fragments)
            if compressed:
                inflater = self.inflater
                data = inflater['object'].decompress(data + b'\x00\x00\xff\xff')
                if inflater['reset']:
                    inflater['object'] = zlib.decompressobj(-inflater['bits'])
            if message_opcode == 0x1:
                return {'text': data.decode('utf-8', 'surrogatepass')}
            return {'binary': data.hex()}

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def build_request(path, host, *, key=DEFAULT_KEY, version='13', headers=None, method='GET', upgrade='websocket',
                  connection='Upgrade', omit=()):
    """Request bytes; ``headers`` is a list of (name, value) appended in order."""
    lines = [f'{method} {path} HTTP/1.1', f'Host: {host}']
    if 'Upgrade' not in omit:
        lines.append(f'Upgrade: {upgrade}')
    if 'Connection' not in omit:
        lines.append(f'Connection: {connection}')
    if 'Sec-WebSocket-Key' not in omit and key is not None:
        lines.append(f'Sec-WebSocket-Key: {key}')
    if 'Sec-WebSocket-Version' not in omit and version is not None:
        lines.append(f'Sec-WebSocket-Version: {version}')
    for name, value in headers or []:
        lines.append(f'{name}: {value}')
    return ('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1')


def random_key():
    return base64.b64encode(os.urandom(16)).decode()
