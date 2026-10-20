"""Measured HTTP read baseline. No ORM, mocks, or correctness-test timing gates."""
import datetime
import json
import math
import platform
import time
import urllib.error
from pathlib import Path

from TESTS.http_client import Client


def benchmark(base_url, fixture, output, samples=50, warmup=5):
    client, _ = Client(base_url).login(fixture['accounts']['missing'])
    results = []
    for path in ('/profile/info', '/personas/list', '/course/list', '/payment/list'):
        print(f'Benchmarking {path}: {warmup} warmup, {samples} samples', flush=True)
        for _ in range(warmup):
            status, _ = client.request('GET', path)
            if status != 200:
                raise AssertionError(f'Warmup failed for {path}: HTTP {status}')
        timings, errors = [], []
        started = time.perf_counter()
        for _ in range(samples):
            before = time.perf_counter()
            try:
                status, _ = client.request('GET', path)
            except (OSError, urllib.error.URLError) as exc:
                status = type(exc).__name__
            timings.append((time.perf_counter() - before) * 1000)
            if status != 200:
                errors.append(status)
        duration = time.perf_counter() - started
        timings.sort()
        percentile = lambda fraction: round(timings[max(0, math.ceil(samples * fraction) - 1)], 3)
        results.append({'path': path, 'samples': samples, 'warmup': warmup, 'errors': errors,
                        'latency_ms': {'p50': percentile(.5), 'p95': percentile(.95), 'p99': percentile(.99)},
                        'throughput_requests_per_second': round(samples / duration, 3),
                        'duration_seconds': round(duration, 3)})
    report = {'measured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'client_platform': platform.platform(), 'client_python': platform.python_version(),
              'concurrency': 1, 'transport': 'HTTP; new connection per request',
              'dataset': fixture['dataset'], 'server_environment': fixture.get('environment', 'caller-supplied'),
              'results': results}
    Path(output).write_text(json.dumps(report, indent=2) + '\n')
    if any(result['errors'] for result in results):
        raise AssertionError('Performance run contained HTTP errors; see results')
    return report
