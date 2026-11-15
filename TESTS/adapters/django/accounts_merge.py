"""Merge the three sharded account suite reports into the full-run report.

`TESTS/adapters/django/accounts.py --shard {tasks,identity,login}` splits the
unsharded suite into three parallel CI jobs. This command proves that the three
shard reports together still describe exactly the unsharded report: the union of
their keys must be the full key set, every key reported twice must agree, and
every shard must have succeeded under the same schema.
"""
import argparse
import json
from pathlib import Path
import sys

SHARDS = ['tasks', 'identity', 'login']

# Every key of the unsharded report: the keys of the full-report dict literal in
# accounts.py plus every `report['x'] = ...` assignment that follows it. The
# shard union must reproduce this set exactly, so keep it in sync with that
# construction; TESTS/test_accounts_shards.py checks that it is.
FULL_REPORT_KEYS = frozenset({
    'actor_authentication_cases',
    'associate_age_update_cases',
    'association_profile_cases',
    'audit_retention_cases',
    'checks',
    'concurrent_guard_requests',
    'concurrent_identity_requests',
    'concurrent_password_requests',
    'concurrent_scoped_reads',
    'credential_concurrency_cases',
    'customer_data',
    'email_balance_reset_groups',
    'identifier_http_contracts_per_backend',
    'identity_selection_cases',
    'identity_state_transitions',
    'image_dispatch',
    'login_bilevel_cases',
    'login_bmp_cases',
    'login_cur_cases',
    'login_finalization_cases',
    'login_gbr_cases',
    'login_gif_cases',
    'login_http',
    'login_ico_cases',
    'login_image_pixel_cases',
    'login_jpeg_cases',
    'login_jpeg_decode_cases',
    'login_jpeg_file_cases',
    'login_jpeg_header_cases',
    'login_jpeg_metadata_cases',
    'login_msp_cases',
    'login_pcd_cases',
    'login_pcx_cases',
    'login_pixar_cases',
    'login_png_file_cases',
    'login_png_header_cases',
    'login_png_pixel_cases',
    'login_png_stream_cases',
    'login_ppm_cases',
    'login_profile_cases',
    'login_qoi_cases',
    'login_response_cases',
    'login_session_cases',
    'login_sgi_cases',
    'login_sun_cases',
    'login_tga_cases',
    'login_transport_contracts_per_backend',
    'login_webp_cases',
    'login_xbm_cases',
    'medical_orphan_cleanup_cases',
    'membership_card_configuration_cases',
    'password_admission_cases',
    'password_batch_sizes',
    'password_selection_cases',
    'payment_description_cases',
    'payment_save_input_cases',
    'profile_images',
    'profile_info',
    'profile_password',
    'profile_tables',
    'profile_update',
    'refresh_http_contracts_per_backend',
    'restore_admission_contracts_per_backend',
    'retired_billing_contracts_per_backend',
    'schema',
    'scoped_batch_sizes',
    'scoped_request_batch_sizes',
    'scoped_request_cases',
    'scoped_resource_cases',
    'second_factor_cases',
    'session_auth_cases',
    'session_http_contracts_per_backend',
    'social_admission_cases',
    'social_configuration_cases',
    'subscription_auto_archive_cases',
    'subscription_date_cases',
    'subscription_token_cleanup_cases',
    'success',
    'synthetic_accounts',
    'two_factor_info_contracts_per_backend',
    'two_factor_setup',
    'two_factor_update',
})


def read_report(directory):
    path = Path(directory)
    if path.is_dir():
        path = path / 'results.json'
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f'{path}: unreadable account shard report ({error})')
    if not isinstance(report, dict):
        raise ValueError(f'{path}: account shard report is not an object')
    return report


def merge(directories):
    reports = {}
    for directory in directories:
        report = read_report(directory)
        shard = report.get('shard')
        if shard not in SHARDS:
            raise ValueError(f'{directory}: not an account shard report (shard={shard!r})')
        if shard in reports:
            raise ValueError(f'{shard}: shard reported more than once')
        if report.get('success') is not True:
            raise ValueError(f'{shard}: shard did not succeed ({report.get("success")!r})')
        reports[shard] = report
    missing = [shard for shard in SHARDS if shard not in reports]
    if missing:
        raise ValueError('missing account shards: ' + ', '.join(missing))
    schemas = {report['schema'] for report in reports.values() if 'schema' in report}
    if len(schemas) > 1:
        raise ValueError('account shards disagree on schema: ' + ', '.join(sorted(map(str, schemas))))
    merged = {'success': True}
    for shard in SHARDS:
        for key, value in reports[shard].items():
            if key in ('shard', 'success'):
                continue
            if key in merged and merged[key] != value:
                raise ValueError(f'{key}: account shards disagree ({merged[key]!r} != {value!r})')
            merged[key] = value
    keys = {key for report in reports.values() for key in report} - {'shard'}
    if keys != FULL_REPORT_KEYS:
        extra = sorted(keys - FULL_REPORT_KEYS)
        absent = sorted(FULL_REPORT_KEYS - keys)
        raise ValueError(f'shard keys do not match the full account report; extra={extra} missing={absent}')
    merged['shards'] = list(SHARDS)
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', required=True, metavar='DIR',
                        help='shard report directories or results.json files; all three shards are required')
    parser.add_argument('--output', required=True, help='merged report path')
    options = parser.parse_args(argv)
    try:
        report = merge(options.input)
    except ValueError as error:
        print(f'account shard merge failed: {error}', file=sys.stderr)
        return 1
    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'success': report['success'], 'shards': report['shards'],
                      'keys': len(report) - 1}), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
