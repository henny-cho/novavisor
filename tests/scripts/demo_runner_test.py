import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "demo_runner.py"
SPEC = importlib.util.spec_from_file_location("demo_runner", RUNNER_PATH)
demo_runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo_runner
SPEC.loader.exec_module(demo_runner)


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


class DemoRunnerVerificationTest(unittest.TestCase):
    def verify(self, child, expectations, timeout=10):
        return demo_runner.verify_child_output(
            child,
            expectations,
            timeout,
            clock=child.clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
        )

    def test_qemu_command_uses_stable_low_ecam_and_manifest_devices(self):
        command = demo_runner.build_qemu_cmd(
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
            demo_runner.build_qemu_cmd(
                Path("/tmp/novavisor.elf"),
                "invalid",
                Path("/tmp/demo"),
                {"qemu_devices": "edu", "guests": []},
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
        result = demo_runner.verify_child_output(
            child,
            [{"pattern": "guest-ready"}],
            120,
            clock=clock,
            timeout_error=FakeTimeout,
            eof_error=FakeEof,
            fatal_patterns=demo_runner.FATAL_OUTPUT_PATTERNS,
        )

        self.assertEqual(result.failure, "fatal")
        self.assertEqual(result.pattern, demo_runner.FATAL_OUTPUT_PATTERNS[0])
        self.assertEqual(result.error, "while waiting for /guest-ready/")
        self.assertEqual(result.elapsed_seconds, 0.5)
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

        result = demo_runner.verify_child_output(
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
        self.assertEqual(matches[0], demo_runner.PatternMatch(1, "boot", 2.0, 2.0, 8.0))
        self.assertEqual(matches[1], demo_runner.PatternMatch(2, "ready", 5.0, 3.0, 5.0))

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

        class FakePexpect:
            TIMEOUT = FakeTimeout
            EOF = FakeEof

            @staticmethod
            def spawn(*_args, **_kwargs):
                return child

        prepared = demo_runner.PreparedVerification(
            label="interrupt",
            phase=0,
            command=("fake-qemu",),
            timeout_seconds=10,
            expectations=({"pattern": "ready"},),
        )
        with tempfile.TemporaryDirectory() as directory:
            tail_path = Path(directory) / "interrupt.qemu-tail.log"
            with (
                mock.patch.object(demo_runner, "_require_pexpect", return_value=FakePexpect),
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    demo_runner.run_prepared_verification(prepared, tail_path)

            diagnostics = json.loads(
                demo_runner.diagnostics_path_for_tail(tail_path).read_text()
            )
            self.assertEqual(diagnostics["failure"]["kind"], "interrupted")
            self.assertEqual(diagnostics["termination"], {
                "attempted": True,
                "succeeded": True,
                "error": "",
            })
            self.assertEqual(child.terminate_calls, [True])

    def test_failure_capture_keeps_only_the_recent_32_kib_of_utf8(self):
        capture = demo_runner.OutputCapture(None)
        discarded = "discarded-prefix:"
        payload = discarded + ("한" * (40 * 1024)) + "recent-tail"

        capture.write(payload)

        expected = payload.encode("utf-8")[-32 * 1024:].decode("utf-8", errors="ignore")
        self.assertLessEqual(len(capture.tail.encode("utf-8")), 32 * 1024)
        self.assertEqual(capture.tail, expected)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            demo_runner.print_failure_tail(capture)
        self.assertNotIn(discarded, stderr.getvalue())
        self.assertIn("recent-tail", stderr.getvalue())

    def test_restore_metric_parser_handles_chunk_boundaries(self):
        capture = demo_runner.OutputCapture(None)
        capture.write("[core_vcpu] VM 2 resto")
        capture.write("red 8192/67108864 bytes in 23")
        capture.write(" ms\r\n")

        self.assertEqual(capture.restore_metrics, [
            demo_runner.RestoreMetric(
                vm=2,
                written_bytes=8192,
                examined_bytes=67108864,
                elapsed_ms=23,
            ),
        ])

    def test_restore_metrics_keep_unterminated_last_line_and_recent_records(self):
        capture = demo_runner.OutputCapture(None)
        for vm in range(35):
            terminator = "\n" if vm < 34 else ""
            capture.write(
                f"[core_vcpu] VM {vm} restored {vm}/{vm + 1} bytes in {vm + 2} ms"
                f"{terminator}"
            )
        capture.finish_metrics()

        self.assertEqual(len(capture.restore_metrics), 32)
        self.assertEqual(capture.restore_metrics[0].vm, 3)
        self.assertEqual(capture.restore_metrics[-1], demo_runner.RestoreMetric(
            vm=34,
            written_bytes=34,
            examined_bytes=35,
            elapsed_ms=36,
        ))

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

        attempts = demo_runner.run_repeated_verification(
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
        attempt = demo_runner.RepeatAttempt(1, "fail", 2.125, "RuntimeError: broken")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"

            demo_runner.initialize_repeat_summary(path)
            demo_runner.append_repeat_summary(path, attempt)

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

        clock = FakeClock()
        child = LoggingFailureChild(clock)

        class FakePexpect:
            TIMEOUT = FakeTimeout
            EOF = FakeEof

            @staticmethod
            def spawn(*_args, **_kwargs):
                return child

        prepared = demo_runner.PreparedVerification(
            label="fake",
            phase=0,
            command=("fake-qemu",),
            timeout_seconds=10,
            expectations=({"pattern": "ready"},),
        )
        with tempfile.TemporaryDirectory() as directory:
            tail_path = Path(directory) / "attempt.qemu-tail.log"
            with (
                mock.patch.object(demo_runner, "_require_pexpect", return_value=FakePexpect),
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                return_code = demo_runner.run_prepared_verification(prepared, tail_path)

            self.assertEqual(return_code, 1)
            tail = tail_path.read_text()
            self.assertLessEqual(len(tail.encode("utf-8")), 32 * 1024)
            self.assertNotIn("discarded:", tail)
            self.assertTrue(tail.endswith("recent"))
            self.assertEqual(child.terminate_calls, [True])
            diagnostics = json.loads(
                demo_runner.diagnostics_path_for_tail(tail_path).read_text()
            )
            self.assertEqual(diagnostics["failure"]["kind"], "exception")
            self.assertEqual(
                diagnostics["failure"]["error"],
                "RuntimeError: expect failed",
            )
            self.assertIn("raise RuntimeError", diagnostics["failure"]["traceback"])
            self.assertEqual(diagnostics["termination"], {
                "attempted": True,
                "succeeded": True,
                "error": "",
            })
            self.assertEqual(diagnostics["restore_metrics"], [{
                "vm": 1,
                "written_bytes": 4096,
                "examined_bytes": 134217728,
                "elapsed_ms": 17,
            }])

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

        class FakePexpect:
            TIMEOUT = FakeTimeout
            EOF = FakeEof

            @staticmethod
            def spawn(*_args, **_kwargs):
                return child

        prepared = demo_runner.PreparedVerification(
            label="timeout",
            phase=0,
            command=("fake-qemu",),
            timeout_seconds=10,
            expectations=({"pattern": "ready", "within_seconds": 6},),
        )
        with tempfile.TemporaryDirectory() as directory:
            tail_path = Path(directory) / "timeout.qemu-tail.log"
            stderr = io.StringIO()
            with (
                mock.patch.object(demo_runner, "_require_pexpect", return_value=FakePexpect),
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
                mock.patch.object(demo_runner.time, "monotonic", side_effect=child.clock),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(stderr),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    demo_runner.run_prepared_verification(prepared, tail_path)

            self.assertIn("timeout waiting for /ready/", stderr.getvalue())
            self.assertIn("QEMU cleanup: KeyboardInterrupt", stderr.getvalue())
            diagnostics = json.loads(
                demo_runner.diagnostics_path_for_tail(tail_path).read_text()
            )
            self.assertEqual(diagnostics["failure"]["kind"], "timeout")
            self.assertEqual(diagnostics["failure"]["pattern"], "ready")
            self.assertEqual(diagnostics["failure"]["elapsed_seconds"], 6.0)
            self.assertEqual(diagnostics["failure"]["remaining_seconds"], 4.0)
            self.assertEqual(diagnostics["termination"], {
                "attempted": True,
                "succeeded": False,
                "error": "KeyboardInterrupt",
            })

    def test_spawn_failure_is_recorded_without_stopping_the_suite(self):
        class FakePexpect:
            TIMEOUT = FakeTimeout
            EOF = FakeEof

            @staticmethod
            def spawn(*_args, **_kwargs):
                raise OSError("spawn failed")

        prepared = demo_runner.PreparedVerification(
            label="spawn",
            phase=0,
            command=("missing-qemu",),
            timeout_seconds=10,
            expectations=({"pattern": "ready"},),
        )
        with tempfile.TemporaryDirectory() as directory:
            tail_path = Path(directory) / "spawn.qemu-tail.log"
            with (
                mock.patch.object(demo_runner, "_require_pexpect", return_value=FakePexpect),
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                return_code = demo_runner.run_prepared_verification(prepared, tail_path)

            self.assertEqual(return_code, 1)
            diagnostics = json.loads(
                demo_runner.diagnostics_path_for_tail(tail_path).read_text()
            )
            self.assertEqual(diagnostics["failure"]["kind"], "spawn")
            self.assertEqual(diagnostics["failure"]["error"], "OSError: spawn failed")
            self.assertIn("raise OSError", diagnostics["failure"]["traceback"])
            self.assertEqual(diagnostics["termination"], {
                "attempted": False,
                "succeeded": False,
                "error": "not attempted: process was not started",
            })


if __name__ == "__main__":
    unittest.main()
