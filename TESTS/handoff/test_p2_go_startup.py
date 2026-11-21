"""Phase 2 (Go API startup gating): the embedded WhatsApp poller can be gated off for a preflight.

Runs the Go contract BE_GO/cmd/server/startup_effects_handoff_test.go (build
tag `handoff`, so `go test ./...` in existing CI never compiles it) with the
real toolchain. Its docstring is the contract:
  startupEffectsFrom(getenv) (startupEffects{WhatsAppOutbox bool}, error);
  ASSOZETA_GO_WHATSAPP_OUTBOX unset/''/'1' runs the loop, '0' never starts it,
  anything else is an error naming the key, raised by run() before it loads
  configuration. The Compose side (api-go receives the caller's value,
  default 1) is pinned in test_p2_process_sets.

Needs Go (BE_GO/go.mod toolchain). Verification only:
ASSOZETA_HANDOFF_P2_GO_OVERLAY=<overlay.json> runs the same tests against a
`go test -overlay` reference or mutation without touching the checkout.

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p2_go_startup
"""
import os
import re
import shutil
import subprocess
import unittest

from TESTS.handoff.topology import ROOT

TESTS = ('TestHandoffStartupEffectsGate', 'TestHandoffInvalidStartupSwitchStopsBeforeConfiguration')


class GoApiStartupGate(unittest.TestCase):
    def test_whatsapp_outbox_is_independently_gateable(self):
        go = shutil.which('go')
        if not go:
            raise AssertionError('the Go toolchain is required for this Phase 2 test')
        command = [go, 'test', '-tags', 'handoff', '-count=1', '-v', '-run', '^(' + '|'.join(TESTS) + ')$']
        overlay = os.environ.get('ASSOZETA_HANDOFF_P2_GO_OVERLAY')
        if overlay:
            command += ['-overlay', overlay]
        result = subprocess.run([*command, './cmd/server'], cwd=ROOT / 'BE_GO', capture_output=True, text=True,
                                timeout=900)
        output = result.stdout + result.stderr
        missing = sorted(set(re.findall(r'undefined: (startupEffectsFrom|startupEffects)\b', output)))
        if missing:
            raise AssertionError('CONTRACT MISSING: BE_GO/cmd/server ' + ', '.join(missing)
                                 + ' (Phase 2 Go API startup gating)')
        self.assertEqual(result.returncode, 0, 'Go API startup gating contract failed:\n' + output[-3000:])
        for name in TESTS:
            self.assertRegex(output, rf'--- PASS: {name} ', f'{name} did not run')


if __name__ == '__main__':
    unittest.main()
