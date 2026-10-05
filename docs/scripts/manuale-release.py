#!/usr/bin/env python3
"""Prepare a local committed manual release candidate; performs no publication."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'BE'))
from application.manuale.publication import prepare_release, verify_release_availability


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run')
    parser.add_argument('--verify-availability', action='store_true')
    parser.add_argument('--prepared')
    parser.add_argument('--evidence-base')
    parser.add_argument('--deployment-environment')
    parser.add_argument('--application-repo', required=True)
    parser.add_argument('--manual-repo', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--release', required=True)
    args = parser.parse_args()
    try:
        if args.verify_availability:
            if not args.prepared or not args.evidence_base or args.run:
                parser.error('--verify-availability requires --prepared and --evidence-base, without --run')
            result = verify_release_availability(args.prepared, args.application_repo, args.manual_repo, args.output,
                release=args.release, evidence_base=args.evidence_base, deployment_environment=args.deployment_environment)
        else:
            if not args.run:
                parser.error('Preparation requires --run')
            result = prepare_release(args.run, args.application_repo, args.manual_repo, args.output, release=args.release)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, 'Manual release preparation rejected: ' + str(exc) + '\n')
    print(json.dumps({'status': result['status'], 'external_actions': [],
        'output': str(Path(args.output).resolve()), 'candidate_corpus_identity': result['candidate_corpus_identity']}, sort_keys=True))
    if args.verify_availability and result['status'] != 'availability-verified':
        parser.exit(1, 'Availability verification did not authorize a published corpus; see availability-report.json.\n')


if __name__ == '__main__':
    main()
