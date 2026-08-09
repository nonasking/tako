"""run() 의 최상위 예외 처리 단위 테스트.

지키려는 계약:
  - 예상 못 한 예외는 버전과 제보 경로를 남긴다 (사용자 0 명일 때 리포트가 오게 하는 유일한 장치)
  - SystemExit 은 가로채지 않는다 — 각 커맨드가 정한 종료 코드가 그대로 나가야 한다
  - 파이프가 먼저 닫히는 건 오류가 아니다 (`tako list | head`)

실행: python -m unittest tests.test_run_errors
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from tako import __version__
from tako.main import ISSUE_URL, run


class UnexpectedErrorTest(unittest.TestCase):
    def test_reports_version_and_issue_url(self) -> None:
        with patch("tako.main._dispatch", side_effect=RuntimeError("boom")), \
             redirect_stderr(io.StringIO()) as err:
            code = run([])

        out = err.getvalue()
        self.assertEqual(code, 1)
        self.assertIn(__version__, out)      # 버전 없는 리포트는 대부분 쓸모없다
        self.assertIn(ISSUE_URL, out)
        self.assertIn("boom", out)           # 스택트레이스도 함께 남아야 한다

    def test_traceback_is_preserved(self) -> None:
        with patch("tako.main._dispatch", side_effect=ValueError("nope")), \
             redirect_stderr(io.StringIO()) as err:
            run([])
        self.assertIn("Traceback", err.getvalue())


class PassthroughTest(unittest.TestCase):
    def test_system_exit_is_not_swallowed(self) -> None:
        # 커맨드들은 SystemExit 으로 종료 코드를 정한다 — 이걸 삼키면 전부 0 이 된다.
        with patch("tako.main._dispatch", side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit) as ctx:
                run([])
        self.assertEqual(ctx.exception.code, 2)

    def test_normal_return_value_passes_through(self) -> None:
        with patch("tako.main._dispatch", return_value=0):
            self.assertEqual(run([]), 0)
        with patch("tako.main._dispatch", return_value=2):
            self.assertEqual(run([]), 2)


class InterruptTest(unittest.TestCase):
    def test_keyboard_interrupt_exits_130(self) -> None:
        with patch("tako.main._dispatch", side_effect=KeyboardInterrupt), \
             redirect_stderr(io.StringIO()) as err:
            code = run([])
        self.assertEqual(code, 130)
        # Ctrl+C 는 버그가 아니다 — 제보 안내를 띄우면 안 된다.
        self.assertNotIn(ISSUE_URL, err.getvalue())

    def test_broken_pipe_is_not_an_error(self) -> None:
        # `tako list | head` — 뒤쪽이 먼저 닫힌 것뿐이다.
        with patch("tako.main._dispatch", side_effect=BrokenPipeError), \
             redirect_stderr(io.StringIO()) as err:
            code = run([])
        self.assertEqual(code, 0)
        self.assertNotIn(ISSUE_URL, err.getvalue())


if __name__ == "__main__":
    unittest.main()
