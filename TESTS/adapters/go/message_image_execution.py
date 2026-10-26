"""POSIX main-thread cooperative cancellation for the bounded image gate.

Import is inert. One interruption unwinds existing finally blocks; a separate
cleanup grace timer bounds unwinding. Platform SIGKILL cannot run Python cleanup.
"""
import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import signal
import sys


_ACTIVE = ContextVar('message_image_execution', default=None)


def begin_cleanup():
    """Declare terminal runner teardown; standalone runners keep their behavior.

    Keep the execution timer until the first cancellation, including SIGALRM.
    That event starts one grace period without unwinding a cleanup operation.
    Call only once business execution is finished; subsequent calls are harmless.
    """
    control = _ACTIVE.get()
    if control is not None:
        control.begin_cleanup(preserve_deadline=True)


@contextmanager
def defer_interruption():
    """Finish a short ownership transition before delivering cancellation."""
    control = _ACTIVE.get()
    if control is None:
        yield
        return
    control.deferred += 1
    try:
        yield
    finally:
        control.deferred -= 1
        if control.interrupted and not control.delivered and not control.deferred:
            control.delivered = True
            raise ExecutionInterrupted()


class ExecutionInterrupted(BaseException):
    """Cancellation that application Exception handlers should not swallow."""


def bounded_seconds(value, maximum):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError('execution.deadline')
    return value


class ExecutionDeadline:
    """Own SIGINT/TERM/ALRM in the main thread until cleanup/receipt completion.

    The first event raises once during execution, even if the runner catches it.
    Declared terminal runner cleanup records that event without unwinding. Further
    INT/TERM events do not restart or interrupt cleanup. Grace expiry is a hard
    exit after best-effort failure recording, never verified cleanup.
    """
    def __init__(self, deadline, cleanup_grace=180, *, on_escalation=None):
        self.deadline = bounded_seconds(deadline, 3600)
        self.cleanup_grace = bounded_seconds(cleanup_grace, 300)
        self.on_escalation = on_escalation
        self.reason = None
        self.escalated = False
        self.previous = {}
        self.cleaning = False
        self.grace_started = False
        self.deferred = 0
        self.delivered = False
        self.token = None

    @property
    def interrupted(self):
        return self.reason is not None

    def begin_cleanup(self, *, preserve_deadline=False):
        self.cleaning = True
        if not preserve_deadline and not self.grace_started:
            self.grace_started = True
            signal.setitimer(signal.ITIMER_REAL, self.cleanup_grace)

    def _handle(self, signum, frame):
        if self.grace_started and signum == signal.SIGALRM:
            self.reason = self.reason or 'cleanup-deadline'
            self.escalated = True
            try:
                if self.on_escalation is not None:
                    self.on_escalation(self)
            finally:
                try:
                    os.write(2, b'image.execution-grace-expired; cleanup unverified\n')
                finally:
                    os._exit(124)
        if self.interrupted:
            return
        self.reason = {signal.SIGALRM: 'deadline', signal.SIGINT: 'sigint',
                       signal.SIGTERM: 'sigterm'}[signum]
        was_cleaning = self.cleaning
        self.begin_cleanup()
        if was_cleaning:
            # Explicit terminal teardown must finish its owned removal loop.
            self.delivered = True
            return
        if not self.deferred:
            self.delivered = True
            raise ExecutionInterrupted()

    def __enter__(self):
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise RuntimeError('execution.alarm-in-use')
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
                self.previous[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle)
            self.token = _ACTIVE.set(self)
            signal.setitimer(signal.ITIMER_REAL, self.deadline)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, kind, value, traceback):
        if self.token is not None:
            _ACTIVE.reset(self.token)
            self.token = None
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)


def fail_receipt(path, execution, *, code='execution.interrupted'):
    """Invalidate only a fresh invocation's receipt, retaining verified evidence.

    Callers check output freshness before invocation. On escalation all cleanup
    claims become unverified. A hard platform kill can prevent even this write.
    """
    path = Path(path)
    if not path.parent.is_dir():
        return
    try:
        report = json.loads(path.read_bytes())
        if type(report) is not dict:
            report = {}
    except (OSError, ValueError):
        report = {}
    report['success'] = False
    report['execution'] = {'interrupted': execution.interrupted,
                           'reason': execution.reason, 'escalated': execution.escalated}
    errors = report.setdefault('errors', [])
    if code not in errors:
        errors.append(code)
    if execution.escalated:
        report['cleanup_verified'] = False
        report['private_directory_removed'] = False
        if type(report.get('runtime')) is dict:
            report['runtime'].update(success=False, cleanup_verified=False, private_directory_removed=False)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')


def run_packaged(argv, *, deadline=1500, cleanup_grace=180, runner=None):
    """Invoke existing runner.main in this protected process; return 0 or 1.

    argv is the existing runner argument list, including one fresh --output.
    runner is solely an offline seam; production always imports the existing main.
    """
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    if sys.flags.optimize:
        raise RuntimeError('execution.optimized-python')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output', type=Path, required=True)
    options, _ = parser.parse_known_args(argv)
    output = options.output.resolve()
    if output.exists():
        raise FileExistsError('execution.output-exists')
    receipt = output / 'results.json'
    control = ExecutionDeadline(deadline, cleanup_grace,
        on_escalation=lambda state: fail_receipt(receipt, state, code='execution.cleanup-grace-expired'))
    with control:
        try:
            if runner is None:
                from TESTS.adapters.go.message_read_scopes_image import main as runner
            code = runner(argv)
        except BaseException:
            code = 1
        control.begin_cleanup()
    # Recheck after handler restoration, including a signal arriving during the
    # return/receipt transition rather than during runner.main itself.
    if control.interrupted or code != 0:
        fail_receipt(receipt, control,
                     code='execution.interrupted' if control.interrupted else 'execution.runner-failed')
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadline', type=float, default=1500)
    parser.add_argument('--cleanup-grace', type=float, default=180)
    parser.add_argument('runner_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.runner_args or args.runner_args[0] != '--':
        parser.error('runner arguments must follow --')
    return run_packaged(args.runner_args[1:], deadline=args.deadline, cleanup_grace=args.cleanup_grace)


if __name__ == '__main__':
    # The runner imports this canonical module for its cleanup handoff. Avoid
    # owning a different ContextVar in the separate -m __main__ namespace.
    from TESTS.adapters.go.message_image_execution import main as packaged_main
    raise SystemExit(packaged_main())
