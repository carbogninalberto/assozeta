"""Build a probe of the installation's Caddy listener, including its virtual host."""
from urllib.parse import urlsplit

from common import compose_command, read_env


def probe(root, env_file, execute):
    values = read_env(env_file)
    public = urlsplit(values.get('APP_URL', ''))
    if public.scheme not in ('http', 'https') or not public.hostname:
        raise ValueError('missing installation URL')
    curl = ['--fail', '--silent', '--show-error', '--connect-timeout', '5', '--max-time', '10']
    site = values.get('SITE_ADDRESS')
    if not site:
        return ['curl', *curl], public.geturl().rstrip('/')
    origin = urlsplit(site if '://' in site else public.scheme + '://' + site)
    if origin.scheme not in ('http', 'https') or not origin.hostname:
        raise ValueError('invalid Caddy site address')
    compose = compose_command(root, env_file)
    if origin.scheme == 'http':
        # Also covers TLS terminated by a front proxy. The Caddy image has wget.
        return (compose + ['exec', '-T', 'web', 'wget', '-T', '10', '-q', '-O', '/dev/null',
                           '--header', 'Host: ' + public.netloc],
                'http://127.0.0.1:' + str(origin.port or 80))
    # Preserve SNI and certificate verification for Caddy-managed TLS. Run the
    # already-installed updater's curl inside the web container's network namespace.
    web = execute(compose + ['ps', '-q', 'web']).strip()
    if not web or len(web.split()) != 1:
        raise RuntimeError('Caddy container unavailable')
    image = values.get('ASSOZETA_UPDATER_REF') or (
        'ghcr.io/carbogninalberto/assozeta-updater:' + (values.get('ASSOZETA_VERSION') or 'latest'))
    return (['docker', 'run', '--rm', '--pull=never', '--network', 'container:' + web, '--entrypoint', 'curl', image,
             *curl, '--noproxy', '*', '--connect-to', '::127.0.0.1:' + str(origin.port or 443),
             '--header', 'Host: ' + public.netloc], origin.geturl().rstrip('/'))
