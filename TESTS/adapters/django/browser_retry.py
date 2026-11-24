"""One retry for a browser child that the runner's network killed before it did anything checked.

Some hosted runner pools drop connections (net::ERR_NETWORK_CHANGED) or stall page loads. A child
that died of that before its first checkpoint changed nothing the driver verifies, so it may run
once more. A content assertion (expect(...), AssertionError, an "Expected" diff) is never retried,
and neither is anything after the first checkpoint.
"""
from pathlib import Path
import re

NETWORK = re.compile(r'net::ERR_[A-Z0-9_]+|Navigation timeout of \d+ ?ms exceeded'
                     r'|page\.(?:goto|reload|waitForNavigation): Timeout \d+ ?ms exceeded')
CONTENT = re.compile(r'AssertionError|expect\(|Expected[ :]')


def attempt_log(work, label, attempt):
    return Path(work) / f'{label}-attempt-{attempt}.log'


def flush(log, path):
    """Append a child's captured output to the driver log and return it."""
    text = Path(path).read_text(errors='replace') if Path(path).exists() else ''
    log.write(text)
    log.flush()
    return text


def retry(log, path, attempt, checkpoints=None):
    """True when attempt 1 died of a network-class error before any checkpoint (the caller relaunches)."""
    text = flush(log, path)
    started = bool(checkpoints and any(Path(checkpoints).glob('*.json')))
    if attempt == 1 and not started and NETWORK.search(text) and not CONTENT.search(text):
        log.write('browser child failed on a network-class error before its first checkpoint; retrying once\n')
        log.flush()
        return True
    return False
