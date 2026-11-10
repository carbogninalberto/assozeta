"""Which storage addresses the remote copy may connect to.

The same address rules as the owner page (BE/instance/remote_backups.py):
private network addresses are allowed (a MinIO on the LAN), while this
machine, link-local and cloud instance addresses, 0.0.0.0/8 and the
multicast/reserved ranges are not. They are applied twice:

- to the saved or proposed endpoint as written, before a client is built;
- to every address a host name resolves to, when the S3 client opens a
  connection. The connection then goes to the address that was checked (the
  host name stays in the TLS server name and the Host header), so a name
  that resolves differently a moment later cannot change where it goes.
"""
import ipaddress
import re
import socket

BLOCKED_MESSAGE = 'Questo indirizzo non può essere usato per la copia remota.'
INVALID_MESSAGE = 'Inserisci un indirizzo valido, ad esempio https://s3.example.com.'
ENDPOINT = re.compile(r'(https?)://([^/?#@ \t\n\r\f\v]+)(/[^?# \t\n\r\f\v]*)?')
AUTHORITY = re.compile(r'(?:\[([0-9A-Fa-f:.]+)\]|([^:\[\]]+))(?::([0-9]{1,5}))?')
DOTTED_QUAD = re.compile(r'(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}')
NUMERIC_LABEL = re.compile(r'[0-9]+|0[xX][0-9A-Fa-f]*')
BLOCKED_NAMES = {'localhost', 'metadata', 'metadata.google.internal', 'instance-data'}
BLOCKED_NETWORKS = [ipaddress.ip_network(value) for value in (
    '0.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '224.0.0.0/3', '100.100.100.200/32', '168.63.129.16/32',
    '::/128', '::1/128', 'fe80::/10', 'fec0::/10', 'ff00::/8', 'fd00:ec2::254/128')]
EMBEDDED_IPV4 = [ipaddress.ip_network(value) for value in ('::ffff:0:0/96', '64:ff9b::/96')]


class BlockedAddress(OSError):
    """A storage address the remote copy may not connect to."""

    def __init__(self, message=BLOCKED_MESSAGE):
        super().__init__(message)


def ascii_lower(value):
    return ''.join(chr(ord(char) + 32) if 'A' <= char <= 'Z' else char for char in value)


def blocked_address(address):
    if isinstance(address, str):
        address = ipaddress.ip_address(address.split('%', 1)[0])
    for network in EMBEDDED_IPV4:
        if address.version == 6 and address in network:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return any(address in network for network in BLOCKED_NETWORKS if network.version == address.version)


def endpoint_problem(value):
    """None when the endpoint as written may be used, else the owner's message (the scheme is the page's choice)."""
    if not value:
        return None
    form = ENDPOINT.fullmatch(value)
    authority = AUTHORITY.fullmatch(form.group(2)) if form else None
    if not authority or authority.group(3) is not None and not 0 < int(authority.group(3)) <= 65535:
        return INVALID_MESSAGE
    if authority.group(1) is not None:
        try:
            address = ipaddress.IPv6Address(authority.group(1))
        except ValueError:
            return INVALID_MESSAGE
    else:
        host = ascii_lower(authority.group(2)).rstrip('.')
        if not host:
            return INVALID_MESSAGE
        address = None
        if NUMERIC_LABEL.fullmatch(host.rsplit('.', 1)[-1]):
            if not DOTTED_QUAD.fullmatch(host) or any(int(part) > 255 for part in host.split('.')):
                return INVALID_MESSAGE
            address = ipaddress.IPv4Address(host)
        elif host in BLOCKED_NAMES or host.endswith('.localhost'):
            return BLOCKED_MESSAGE
    if address is not None and blocked_address(address):
        return BLOCKED_MESSAGE
    return None


def vetted_addresses(host, port, resolve=socket.getaddrinfo):
    """Every address `host` resolves to, refused as a whole when one of them is not allowed."""
    name = ascii_lower(host).rstrip('.')
    if name in BLOCKED_NAMES or name.endswith('.localhost'):
        raise BlockedAddress()
    found = []
    for family, _, _, _, sockaddr in resolve(host, port, type=socket.SOCK_STREAM):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        if blocked_address(sockaddr[0]):
            raise BlockedAddress()
        if sockaddr[0] not in found:
            found.append(sockaddr[0])
    if not found:
        raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')
    return found


def blocked_in(exc):
    """True when a BlockedAddress is anywhere in an exception's chain (botocore wraps connection errors)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, BlockedAddress):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__ or (exc.args[0] if exc.args and isinstance(exc.args[0], BaseException) else None) \
            or getattr(exc, 'kwargs', {}).get('error')
    return False


class _Vetted:
    """urllib3 connection mixin: connect only to a checked address of the host."""
    resolve = staticmethod(socket.getaddrinfo)

    def _new_conn(self):
        host = self._dns_host
        addresses = vetted_addresses(host, self.port, self.resolve)
        last = None
        for address in addresses:
            self._dns_host = address
            try:
                return super()._new_conn()
            except OSError as exc:
                last = exc
            finally:
                self._dns_host = host
        raise last


def pin_client(client):
    """Route a boto3 client's connections through the address check."""
    from botocore.awsrequest import AWSHTTPConnection, AWSHTTPConnectionPool, AWSHTTPSConnection, AWSHTTPSConnectionPool

    class VettedHTTPConnection(_Vetted, AWSHTTPConnection):
        pass

    class VettedHTTPSConnection(_Vetted, AWSHTTPSConnection):
        pass

    class VettedHTTPConnectionPool(AWSHTTPConnectionPool):
        ConnectionCls = VettedHTTPConnection

    class VettedHTTPSConnectionPool(AWSHTTPSConnectionPool):
        ConnectionCls = VettedHTTPSConnection

    session = client._endpoint.http_session
    # The same dict is shared with the session's pool managers.
    session._pool_classes_by_scheme.update({'http': VettedHTTPConnectionPool, 'https': VettedHTTPSConnectionPool})
    return client
