"""Retired v1 archive sections accepted for compatibility, never restored.

Keep this allowlist explicit. Business records and unknown models do not become
skippable just because the current exporter no longer writes them.
"""
from .json_stream import iter_json_array

DEPRECATED_SECTIONS = {
    'BillingPlan': 'system/00_billing_plans.json',
    'PreviewAndCustomFeatures': 'system/64_preview_features.json',
}


def skipped_records(archive, manifest):
    counts = {}
    for spec in manifest.get('models_exported', []):
        name = spec.get('name')
        if name not in DEPRECATED_SECTIONS:
            continue
        filename = spec.get('file')
        if name in counts or filename != DEPRECATED_SECTIONS[name]:
            raise ValueError(f'Duplicate or invalid retired section: {name}')
        try:
            with archive.open(filename) as source:
                count = 0
                for row in iter_json_array(source):
                    if not isinstance(row, dict):
                        raise ValueError(f'Invalid retired record: {name}')
                    count += 1
        except KeyError as exc:
            raise ValueError(f'Missing retired section: {filename}') from exc
        if type(spec.get('count')) is not int or spec['count'] != count:
            raise ValueError(f'Invalid retired record count: {name}')
        counts[name] = count
    return counts
