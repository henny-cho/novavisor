import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from novakit.core import board, config  # noqa: E402
from novakit.services import (  # noqa: E402
    artifacts,
    cmake,
    expect,
    manifest,
    report,
    spawn,
    verify,
)


class FakeTimeout(Exception):
    pass


class FakeEof(Exception):
    pass


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeChild:
    def __init__(self, clock, actions=None, terminate_result=True, terminate_error=None):
        self.clock = clock
        self.actions = list(actions or [])
        self.events = []
        self.terminate_calls = []
        self.terminate_result = terminate_result
        self.terminate_error = terminate_error

    def expect(self, pattern, timeout):
        self.events.append(("expect", pattern, timeout))
        action = self.actions.pop(0) if self.actions else None
        if isinstance(action, BaseException):
            raise action
        if action is not None:
            self.clock.advance(action)

    def send(self, data):
        self.events.append(("send", data))

    def terminate(self, force=False):
        self.terminate_calls.append(force)
        if self.terminate_error is not None:
            raise self.terminate_error
        return self.terminate_result


class Ran(NamedTuple):
    """What one `run_scenario` left behind."""

    code: int | None
    console: str
    tail: Path
    diagnostics: dict


class ScenarioHarness(unittest.TestCase):
    """Driving `verify.run_scenario` against a stand-in child.

    Four tests want the same four things around it — a pexpect that
    hands back their child, a scenario to run, somewhere to put the tail
    log, and the diagnostics written beside it — and differ only in what
    the child does and what they then assert. Kept together, the setup
    is one thing to read and the tests are their own subject.
    """

    def observe(self, child=None, *, label, launch=None, expectations=None,
                interrupt=False, clock=False) -> Ran:
        """Run one scenario and hand back what it wrote.

        `launch` replaces the spawn itself, for a test about a launch
        that never produces a child; `interrupt` is for the children
        whose failure escapes rather than being reported; and `clock` for
        the one test that reads a duration out of the diagnostics and so
        needs the module's clock to be the child's.
        """

        def hand_over(*_args, **_kwargs):
            return child

        class FakePexpect:
            TIMEOUT = FakeTimeout
            EOF = FakeEof
            spawn = staticmethod(launch or hand_over)

        prepared = expect.Scenario(
            label=label,
            phase=0,
            command=("fake-qemu",),
            timeout_seconds=10,
            expectations=tuple(expectations or ({"pattern": "ready"},)),
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tail_path = Path(directory.name) / f"{label}.qemu-tail.log"
        console = io.StringIO()
        patches = [
            mock.patch.object(spawn, "_require_pexpect", return_value=FakePexpect),
            mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(console),
        ]
        if clock:
            patches.append(mock.patch.object(spawn.time, "monotonic", side_effect=child.clock))

        code = None
        with contextlib.ExitStack() as opened:
            for patch in patches:
                opened.enter_context(patch)
            if interrupt:
                # Entered last, so it unwinds first and takes the escape
                # before the redirects come off.
                opened.enter_context(self.assertRaises(KeyboardInterrupt))
            code = verify.run_scenario(prepared, verify.Sink(tail=tail_path), scope="nova demo")
        return Ran(
            code,
            console.getvalue(),
            tail_path,
            json.loads(report.diagnostics_path_for_tail(tail_path).read_text()),
        )


class DemoRunnerVerificationTest(ScenarioHarness):
    def verify(self, child, expectations, timeout=10):
        return expect.observe_output(
            child,
            expectations,
            timeout,
            clock=child.clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
        )

    def test_hypervisor_build_accepts_an_explicit_profile(self):
        spec = cmake.BuildSpec.of(preset="aarch64-standard-release")

        self.assertEqual(spec.preset, "aarch64-standard-release")
        self.assertEqual(spec.config_path, config.DEFAULT_CONFIG)

    def test_qemu_command_uses_stable_low_ecam_and_manifest_devices(self):
        command = artifacts.build_qemu_cmd(
            Path("/tmp/novavisor.elf"),
            "15_dma_isolation",
            Path("/tmp/demo"),
            {
                "qemu_devices": [
                    "edu,bus=pcie.0,addr=2.0,dma_mask=0xffffffffff",
                ],
                "guests": [],
            },
        )

        self.assertIn("virt,virtualization=on,gic-version=3,iommu=smmuv3,highmem-ecam=off", command)
        self.assertIn("none", command)
        self.assertIn("edu,bus=pcie.0,addr=2.0,dma_mask=0xffffffffff", command)

    def test_qemu_command_rejects_invalid_device_list(self):
        with self.assertRaisesRegex(SystemExit, "qemu_devices"):
            artifacts.build_qemu_cmd(
                Path("/tmp/novavisor.elf"),
                "invalid",
                Path("/tmp/demo"),
                {"qemu_devices": "edu", "guests": []},
            )

    def test_embedded_qemu_command_omits_external_loader(self):
        command = artifacts.build_qemu_cmd(
            Path("/tmp/novavisor.elf"),
            "embedded",
            Path("/tmp/demo"),
            {
                "payload_mode": "embedded",
                "guests": [{
                    "name": "guest",
                    "binary": "guest.bin",
                    "load_addr": 0x50000000,
                    "entry": 0x50000000,
                    "memory_size": 0x100000,
                }],
            },
        )

        self.assertFalse(any("loader,file=" in argument for argument in command))

    def test_embedded_payload_manifest_pins_binary_digest_and_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "guest.bin"
            binary.write_bytes(b"guest image")
            manifest = {
                "payload_mode": "embedded",
                "guests": [{
                    "name": "guest",
                    "binary": "guest.bin",
                    "load_addr": 0x50000000,
                    "entry": 0x50000000,
                    "memory_size": 0x100000,
                }],
            }
            with (
                mock.patch.object(config, "BUILD_ROOT", root / "build"),
                mock.patch.object(
                    artifacts, "resolve_guest_binary", return_value=binary
                ),
            ):
                path = artifacts.prepare_payload_manifest(
                    "embedded", root, manifest
                )

            payload = json.loads(path.read_text())["payloads"][0]
            self.assertEqual(payload["binary"], str(binary))
            self.assertEqual(payload["load_pa"], 0x50000000)
            self.assertEqual(
                payload["sha256"], hashlib.sha256(b"guest image").hexdigest()
            )

    def test_scenario_deadline_bounds_each_pattern_wait(self):
        clock = FakeClock()
        child = FakeChild(clock, actions=[8.0, 1.0])

        result = self.verify(child, [
            {"pattern": "boot", "within_seconds": 9},
            {"pattern": "ready", "within_seconds": 100},
        ])

        self.assertTrue(result.ok)
        self.assertEqual(child.events[0], ("expect", "boot", 9.0))
        self.assertEqual(child.events[1], ("expect", "ready", 2.0))

    def test_expired_scenario_does_not_accept_a_buffered_match(self):
        clock = FakeClock()
        child = FakeChild(clock, actions=[10.0])

        result = self.verify(child, [
            {"pattern": "boot"},
            {"pattern": "late-but-buffered"},
        ])

        self.assertEqual(result.failure, "timeout")
        self.assertEqual(result.pattern, "late-but-buffered")
        self.assertEqual(child.events, [("expect", "boot", 10.0)])

    def test_pattern_returned_after_its_deadline_is_timeout(self):
        clock = FakeClock()
        child = FakeChild(clock, actions=[7.0])

        result = self.verify(child, [
            {"pattern": "late", "within_seconds": 6, "send": "must-not-send"},
        ])

        self.assertEqual(result.failure, "timeout")
        self.assertEqual(result.pattern, "late")
        self.assertEqual(result.elapsed_seconds, 7.0)
        self.assertEqual(result.remaining_seconds, 3.0)
        self.assertEqual(result.matches, ())
        self.assertFalse(any(event[0] == "send" for event in child.events))

    def test_fatal_output_stops_waiting_and_terminates_qemu(self):
        class FatalChild(FakeChild):
            def expect(self, patterns, timeout):
                self.events.append(("expect", patterns, timeout))
                self.clock.advance(0.5)
                return 1

        clock = FakeClock()
        child = FatalChild(clock)
        result = expect.observe_output(
            child,
            [{"pattern": "guest-ready"}],
            120,
            clock=clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
            fatal_patterns=board.FATAL_PATTERNS,
        )

        self.assertEqual(result.failure, "fatal")
        self.assertEqual(result.pattern, board.FATAL_PATTERNS[0])
        self.assertEqual(result.error, "while waiting for /guest-ready/")
        self.assertEqual(result.elapsed_seconds, 0.5)
        self.assertEqual(child.terminate_calls, [True])

    def test_manifest_forbidden_output_stops_before_expected_pattern(self):
        class ForbiddenChild(FakeChild):
            def expect(self, patterns, timeout):
                self.events.append(("expect", patterns, timeout))
                self.clock.advance(0.25)
                return 0

        clock = FakeClock()
        child = ForbiddenChild(clock)
        forbidden = (r"\[dma\] VM 0 resumed generation 4",)
        result = expect.observe_output(
            child,
            [{"pattern": "dma lifecycle boot 3"}],
            20,
            clock=clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
            fatal_patterns=board.FATAL_PATTERNS,
            forbidden_patterns=forbidden,
        )

        self.assertEqual(result.failure, "forbidden")
        self.assertEqual(result.pattern, forbidden[0])
        self.assertEqual(result.error, "while waiting for /dma lifecycle boot 3/")
        self.assertEqual(result.elapsed_seconds, 0.25)
        self.assertEqual(child.terminate_calls, [True])

    def test_manifest_forbid_patterns_are_shared_and_validated(self):
        demo_manifest = {"forbid": ["generation 4"]}
        self.assertEqual(
            manifest.manifest_pattern_list(demo_manifest, "forbid"),
            ("generation 4",),
        )
        for invalid in ("generation 4", [""], ["["]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(SystemExit):
                    manifest.manifest_pattern_list({"forbid": invalid}, "forbid")

    def test_forbidden_output_after_last_expected_is_detected_during_drain(self):
        class BufferedForbiddenChild(FakeChild):
            def __init__(self, clock):
                super().__init__(clock)
                self.expect_calls = 0

            def expect(self, patterns, timeout):
                self.events.append(("expect", patterns, timeout))
                self.expect_calls += 1
                if self.expect_calls == 1:
                    return 1
                return 0

        clock = FakeClock()
        child = BufferedForbiddenChild(clock)
        forbidden = ("generation 4",)
        result = expect.observe_output(
            child,
            [{"pattern": "boot 3"}],
            20,
            clock=clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
            forbidden_patterns=forbidden,
        )

        self.assertEqual(result.failure, "forbidden")
        self.assertEqual(result.pattern, forbidden[0])
        self.assertEqual(result.error, "after all expected output matched")
        self.assertEqual(child.terminate_calls, [True])

    def test_clean_eof_after_last_expected_preserves_success(self):
        class CleanChild(FakeChild):
            def __init__(self, clock):
                super().__init__(clock)
                self.expect_calls = 0

            def expect(self, patterns, timeout):
                self.events.append(("expect", patterns, timeout))
                self.expect_calls += 1
                return 1

        child = CleanChild(FakeClock())
        result = expect.observe_output(
            child,
            [{"pattern": "boot 3"}],
            20,
            clock=child.clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
            forbidden_patterns=("generation 4",),
        )

        self.assertTrue(result.ok)
        self.assertEqual(child.terminate_calls, [True])

    def test_send_occurs_only_after_its_pattern_matches(self):
        clock = FakeClock()
        child = FakeChild(clock)

        result = self.verify(child, [
            {"pattern": "login:", "send": "root\n"},
            {"pattern": "#", "send": "nova-mixed\n"},
        ])

        self.assertTrue(result.ok)
        self.assertEqual(child.events, [
            ("expect", "login:", 10.0),
            ("send", "root\n"),
            ("expect", "#", 10.0),
            ("send", "nova-mixed\n"),
        ])

        timed_out = FakeChild(FakeClock(), actions=[FakeTimeout()])
        result = self.verify(timed_out, [{"pattern": "login:", "send": "root\n"}])
        self.assertEqual(result.failure, "timeout")
        self.assertFalse(any(event[0] == "send" for event in timed_out.events))

    def test_child_is_terminated_on_all_exit_paths(self):
        cases = [
            ("success", [], None),
            ("timeout", [FakeTimeout()], "timeout"),
            ("eof", [FakeEof()], "eof"),
            ("exception", [RuntimeError("boom")], "exception"),
        ]

        for name, actions, failure in cases:
            with self.subTest(name=name):
                child = FakeChild(FakeClock(), actions=actions)
                result = self.verify(child, [{"pattern": "ready"}])
                self.assertEqual(result.failure, failure)
                self.assertEqual(child.terminate_calls, [True])

    def test_child_is_terminated_when_initial_clock_read_fails(self):
        class FailingClock:
            def __call__(self):
                raise RuntimeError("clock failed")

        child = FakeChild(FailingClock())
        result = self.verify(child, [{"pattern": "ready"}])
        self.assertEqual(result.failure, "exception")
        self.assertEqual(result.error, "RuntimeError: clock failed")
        self.assertEqual(child.terminate_calls, [True])

    def test_match_diagnostics_record_pattern_timing(self):
        clock = FakeClock()
        child = FakeChild(clock, actions=[2.0, 3.0])
        matches = []

        result = expect.observe_output(
            child,
            [{"pattern": "boot"}, {"pattern": "ready"}],
            10,
            clock=clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
            on_match=matches.append,
        )

        self.assertTrue(result.ok)
        self.assertEqual(matches, list(result.matches))
        self.assertEqual(matches[0], expect.PatternMatch(1, "boot", 2.0, 2.0, 8.0))
        self.assertEqual(matches[1], expect.PatternMatch(2, "ready", 5.0, 3.0, 5.0))

    def test_timeout_diagnostics_include_elapsed_and_scenario_remaining(self):
        class TimedTimeoutChild(FakeChild):
            def expect(self, pattern, timeout):
                self.events.append(("expect", pattern, timeout))
                self.clock.advance(timeout)
                raise FakeTimeout()

        clock = FakeClock()
        child = TimedTimeoutChild(clock)
        result = self.verify(
            child,
            [{"pattern": "load-done", "within_seconds": 6}],
            timeout=10,
        )

        self.assertEqual(result.failure, "timeout")
        self.assertEqual(result.pattern, "load-done")
        self.assertEqual(result.wait_seconds, 6.0)
        self.assertEqual(result.elapsed_seconds, 6.0)
        self.assertEqual(result.remaining_seconds, 4.0)

    def test_termination_failure_is_a_separate_verification_error(self):
        returned_false = FakeChild(FakeClock(), terminate_result=False)
        result = self.verify(returned_false, [{"pattern": "ready"}])
        self.assertIsNone(result.failure)
        self.assertFalse(result.ok)
        self.assertFalse(result.termination_succeeded)
        self.assertEqual(result.termination_error, "terminate(force=True) returned false")

        raised = FakeChild(FakeClock(), terminate_error=OSError("kill failed"))
        result = self.verify(raised, [{"pattern": "ready"}])
        self.assertFalse(result.ok)
        self.assertEqual(result.termination_error, "OSError: kill failed")

    def test_keyboard_interrupt_preserves_diagnostics_then_propagates(self):
        child = FakeChild(FakeClock(), actions=[KeyboardInterrupt()])
        ran = self.observe(child, label="interrupt", interrupt=True)

        self.assertEqual(ran.diagnostics["failure"]["kind"], "interrupted")
        self.assertEqual(ran.diagnostics["termination"], {
            "attempted": True,
            "succeeded": True,
            "error": "",
        })
        self.assertEqual(child.terminate_calls, [True])

    def test_failure_capture_keeps_only_the_recent_32_kib_of_utf8(self):
        capture = spawn.OutputCapture(None)
        discarded = "discarded-prefix:"
        payload = discarded + ("한" * (40 * 1024)) + "recent-tail"

        capture.write(payload)

        expected = payload.encode("utf-8")[-32 * 1024:].decode("utf-8", errors="ignore")
        self.assertLessEqual(len(capture.tail.encode("utf-8")), 32 * 1024)
        self.assertEqual(capture.tail, expected)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            spawn.print_tail(capture, scope="nova demo")
        self.assertNotIn(discarded, stderr.getvalue())
        self.assertIn("recent-tail", stderr.getvalue())

    def test_repeat_runs_all_attempts_and_records_elapsed_time(self):
        clock = FakeClock()
        outcomes = [
            (1.5, 0),
            (2.0, 1),
            (0.25, RuntimeError("broken")),
            (0.5, SystemExit("missing image")),
        ]
        reported = []

        def verify_once(_number):
            elapsed, outcome = outcomes.pop(0)
            clock.advance(elapsed)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        attempts = expect.run_repeated(
            4,
            verify_once,
            clock=clock,
            on_attempt=reported.append,
        )

        self.assertEqual(
            [attempt.status for attempt in attempts],
            ["pass", "fail", "fail", "fail"],
        )
        self.assertEqual(
            [attempt.elapsed_seconds for attempt in attempts],
            [1.5, 2.0, 0.25, 0.5],
        )
        self.assertEqual(attempts[2].error, "RuntimeError: broken")
        self.assertEqual(attempts[3].error, "SystemExit: missing image")
        self.assertEqual(reported, attempts)

    def test_repeat_summary_is_durable_after_each_attempt(self):
        attempt = expect.RepeatAttempt(1, "fail", 2.125, "RuntimeError: broken")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"

            report.initialize_repeat_summary(path)
            report.append_repeat_summary(path, attempt)

            self.assertEqual(path.read_text().splitlines(), [
                "run,status,elapsed_seconds,error",
                "1,fail,2.125,RuntimeError: broken",
            ])

    def test_unexpected_process_error_preserves_bounded_tail(self):
        class LoggingFailureChild(FakeChild):
            def expect(self, pattern, timeout):
                del pattern, timeout
                self.logfile_read.write(
                    "[core_vcpu] VM 1 restored 4096/134217728 bytes in 17 ms\n"
                    + "discarded:"
                    + ("한" * (40 * 1024))
                    + "recent"
                )
                raise RuntimeError("expect failed")

        child = LoggingFailureChild(FakeClock())
        ran = self.observe(child, label="attempt")

        self.assertEqual(ran.code, 1)
        tail = ran.tail.read_text()
        self.assertLessEqual(len(tail.encode("utf-8")), 32 * 1024)
        self.assertNotIn("discarded:", tail)
        self.assertTrue(tail.endswith("recent"))
        self.assertEqual(child.terminate_calls, [True])
        self.assertEqual(ran.diagnostics["failure"]["kind"], "exception")
        self.assertEqual(
            ran.diagnostics["failure"]["error"],
            "RuntimeError: expect failed",
        )
        self.assertIn("raise RuntimeError", ran.diagnostics["failure"]["traceback"])
        self.assertEqual(ran.diagnostics["termination"], {
            "attempted": True,
            "succeeded": True,
            "error": "",
        })

    def test_timeout_and_cleanup_interrupt_share_console_and_diagnostics(self):
        class TimeoutChild(FakeChild):
            def expect(self, pattern, timeout):
                del pattern
                self.clock.advance(timeout)
                self.logfile_read.write("waiting for guest\n")
                raise FakeTimeout()

        child = TimeoutChild(
            FakeClock(),
            terminate_error=KeyboardInterrupt(),
        )
        ran = self.observe(
            child,
            label="timeout",
            expectations=({"pattern": "ready", "within_seconds": 6},),
            interrupt=True,
            clock=True,
        )

        self.assertIn("timeout waiting for /ready/", ran.console)
        self.assertIn("QEMU cleanup: KeyboardInterrupt", ran.console)
        self.assertEqual(ran.diagnostics["failure"]["kind"], "timeout")
        self.assertEqual(ran.diagnostics["failure"]["pattern"], "ready")
        self.assertEqual(ran.diagnostics["failure"]["elapsed_seconds"], 6.0)
        self.assertEqual(ran.diagnostics["failure"]["remaining_seconds"], 4.0)
        self.assertEqual(ran.diagnostics["termination"], {
            "attempted": True,
            "succeeded": False,
            "error": "KeyboardInterrupt",
        })

    def test_spawn_failure_is_recorded_without_stopping_the_suite(self):
        def spawn_fails(*_args, **_kwargs):
            raise OSError("spawn failed")

        ran = self.observe(label="spawn", launch=spawn_fails)

        self.assertEqual(ran.code, 1)
        self.assertEqual(ran.diagnostics["failure"]["kind"], "spawn")
        self.assertEqual(ran.diagnostics["failure"]["error"], "OSError: spawn failed")
        self.assertIn("raise OSError", ran.diagnostics["failure"]["traceback"])
        self.assertEqual(ran.diagnostics["termination"], {
            "attempted": False,
            "succeeded": False,
            "error": "not attempted: process was not started",
        })


@unittest.skipUnless(importlib.util.find_spec("pexpect"), "pexpect is not installed")
class InvalidOutputBytesTest(unittest.TestCase):
    def test_invalid_utf8_output_does_not_fail_the_drain(self):
        """Firmware console bytes are not guaranteed UTF-8: SMP cores
        interleave multibyte writes and a SIGKILL cuts them anywhere.
        The verifier must decode such output rather than turn a fully
        matched run into a post-termination exception."""
        scenario = expect.Scenario(
            label="demo",
            phase=1,
            command=("/bin/sh", "-c", "printf 'ok\\nabc\\342zz\\n'; sleep 5"),
            timeout_seconds=5,
            expectations=({"pattern": "ok"},),
            forbidden_patterns=("NEVER",),
        )

        run = spawn.observe(scenario, stream=None)

        self.assertTrue(run.result.ok, run.result)


if __name__ == "__main__":
    unittest.main()
