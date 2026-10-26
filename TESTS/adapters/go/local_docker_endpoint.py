"""Select only an explicitly supplied local Unix socket for parent Docker tools."""
import os
import stat


_OVERRIDES = frozenset({
    'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY',
    'DOCKER_CERT_PATH', 'DOCKER_TLS_CERTDIR', 'DOCKER_SSH_COMMAND',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'DOCKER_HTTP_PROXY', 'DOCKER_HTTPS_PROXY', 'DOCKER_NO_PROXY',
})


def _valid_path(path):
    return (isinstance(path, str) and os.path.isabs(path)
            and not path.startswith('//')
            and not any(character in path for character in '\x00\r\n'))


def local_docker_environment(tool_environment: dict, socket_path: str | None) -> dict:
    """Copy the caller's sanitized environment, preserving its private DOCKER_CONFIG.

    None leaves Docker's default local endpoint. An explicit path is resolved and
    checked with stat only; this neither connects nor discovers ambient settings.
    ValueError codes: INVALID_DOCKER_SOCKET_PATH, DOCKER_SOCKET_UNAVAILABLE,
    DOCKER_SOCKET_NOT_SOCKET. Filesystem exception details are never propagated.
    """
    environment = {key: value for key, value in tool_environment.items()
                   if key.upper() not in _OVERRIDES}
    if socket_path is None:
        return environment
    if not _valid_path(socket_path):
        raise ValueError('INVALID_DOCKER_SOCKET_PATH')

    try:
        resolved = os.path.realpath(socket_path, strict=True)
    except (OSError, RuntimeError, UnicodeError):
        resolved = None
    # Raise outside the handler so even exception context cannot retain details.
    if resolved is None:
        raise ValueError('DOCKER_SOCKET_UNAVAILABLE')
    if not _valid_path(resolved):
        raise ValueError('INVALID_DOCKER_SOCKET_PATH')

    try:
        mode = os.stat(resolved).st_mode
    except (OSError, RuntimeError, UnicodeError):
        mode = None
    if mode is None:
        raise ValueError('DOCKER_SOCKET_UNAVAILABLE')
    if not stat.S_ISSOCK(mode):
        raise ValueError('DOCKER_SOCKET_NOT_SOCKET')
    environment['DOCKER_HOST'] = 'unix://' + resolved
    return environment
