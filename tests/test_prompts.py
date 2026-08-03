"""prompts 의 EOF 처리 단위 테스트.

Ctrl+D(EOF) 를 빈 줄과 구분 못 해 ask_text 재시도 루프가 무한 돌던 버그의 회귀 방지.

실행: python -m unittest tests.test_prompts
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr

from tako.prompts import ask_text, confirm


class _StdinPatch:
    def __init__(self, text: str):
        self._text = text

    def __enter__(self):
        self._orig = sys.stdin
        sys.stdin = io.StringIO(self._text)
        return self

    def __exit__(self, *exc):
        sys.stdin = self._orig
        return False


class EofTest(unittest.TestCase):
    def test_ask_text_eof_exits_instead_of_looping(self) -> None:
        # default 없는 ask_text 에서 EOF — 예전엔 "(빈 입력 안 됨)" 무한 루프
        with _StdinPatch(""), redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                ask_text("제목")
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("EOF", err.getvalue())

    def test_ask_text_eof_with_default_also_exits(self) -> None:
        # EOF 는 '기본값 선택' 이 아니라 '입력 중단' — default 가 있어도 종료
        with _StdinPatch(""), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                ask_text("프로젝트", default="WL")

    def test_confirm_eof_exits(self) -> None:
        with _StdinPatch(""), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                confirm("진행?")


class NormalInputTest(unittest.TestCase):
    def test_ask_text_reads_line(self) -> None:
        with _StdinPatch("입력값\n"), redirect_stderr(io.StringIO()):
            self.assertEqual(ask_text("제목"), "입력값")

    def test_empty_line_falls_back_to_default(self) -> None:
        with _StdinPatch("\n"), redirect_stderr(io.StringIO()):
            self.assertEqual(ask_text("프로젝트", default="WL"), "WL")

    def test_confirm_korean_yes(self) -> None:
        with _StdinPatch("ㅇ\n"), redirect_stderr(io.StringIO()):
            self.assertTrue(confirm("진행?", default=False))


if __name__ == "__main__":
    unittest.main()
