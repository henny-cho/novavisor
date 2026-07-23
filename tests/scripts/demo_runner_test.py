import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path


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
    def __init__(self, clock, actions=None):
        self.clock = clock
        self.actions = list(actions or [])
        self.events = []
        self.terminate_calls = []

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
        return True


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
            ("timeout", [FakeTimeout()], None),
            ("eof", [FakeEof()], None),
            ("exception", [RuntimeError("boom")], RuntimeError),
        ]

        for name, actions, raised in cases:
            with self.subTest(name=name):
                child = FakeChild(FakeClock(), actions=actions)
                if raised is None:
                    self.verify(child, [{"pattern": "ready"}])
                else:
                    with self.assertRaises(raised):
                        self.verify(child, [{"pattern": "ready"}])
                self.assertEqual(child.terminate_calls, [True])

    def test_child_is_terminated_when_initial_clock_read_fails(self):
        class FailingClock:
            def __call__(self):
                raise RuntimeError("clock failed")

        child = FakeChild(FailingClock())
        with self.assertRaisesRegex(RuntimeError, "clock failed"):
            self.verify(child, [{"pattern": "ready"}])
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


if __name__ == "__main__":
    unittest.main()
