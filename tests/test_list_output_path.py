"""--output 저장 경로 확정 로직 단위 테스트 (stdlib unittest — 네트워크 없음).

실행: python -m unittest tests.test_list_output_path
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from tako.cmd_common import stamp_kst_str
from tako.cmd_list import _emit, _reserve_output_path


@contextmanager
def quiet():
    """테스트 실행 로그에 안내 문구가 섞이지 않게 stdout/stderr 를 잠깐 삼킨다."""
    with ExitStack() as stack:
        out = stack.enter_context(redirect_stdout(io.StringIO()))
        err = stack.enter_context(redirect_stderr(io.StringIO()))
        yield out, err


class StampTest(unittest.TestCase):
    def test_shape(self) -> None:
        # YYYY-MM-DD_HHMMSS — 파일명에 그대로 넣어도 안전한 문자만
        stamp = stamp_kst_str()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}_\d{6}$")


class ReserveOutputPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_free_path_is_untouched(self) -> None:
        # 비어 있으면 사용자가 준 경로 그대로. 멀쩡한 이름을 괜히 바꾸지 않는다.
        target = self.base / "report.csv"
        self.assertEqual(_reserve_output_path(str(target)), target)

    def test_existing_file_gets_stamp_before_extension(self) -> None:
        target = self.base / "report.csv"
        target.write_text("먼저 있던 내용", encoding="utf-8")

        with quiet() as (_, err):
            reserved = _reserve_output_path(str(target))
        self.assertIn("이미 있음", err.getvalue())  # 조용히 비켜가지 말고 알려야
        self.assertNotEqual(reserved, target)
        self.assertEqual(reserved.suffix, ".csv")  # 엑셀 연결 유지
        self.assertTrue(reserved.name.startswith("report-"))
        self.assertRegex(reserved.name, r"^report-\d{4}-\d{2}-\d{2}_\d{6}\.csv$")
        # 원본은 그대로 남아 있어야
        self.assertEqual(target.read_text(encoding="utf-8"), "먼저 있던 내용")

    def test_same_second_collision_gets_counter(self) -> None:
        target = self.base / "report.csv"
        target.write_text("x", encoding="utf-8")

        with quiet():
            first = _reserve_output_path(str(target))
            first.write_text("y", encoding="utf-8")
            second = _reserve_output_path(str(target))

        self.assertNotEqual(first, second)
        self.assertEqual(second.suffix, ".csv")
        # 같은 초에 겹치면 -2 가 붙는다 (초가 넘어갔으면 스탬프 자체가 달라짐)
        if second.name.startswith(first.stem):
            self.assertEqual(second.name, f"{first.stem}-2.csv")

    def test_no_extension(self) -> None:
        target = self.base / "report"
        target.write_text("x", encoding="utf-8")
        with quiet():
            reserved = _reserve_output_path(str(target))
        self.assertRegex(reserved.name, r"^report-\d{4}-\d{2}-\d{2}_\d{6}$")

    def test_missing_parent_dir_is_created(self) -> None:
        target = self.base / "없던폴더" / "안쪽" / "report.csv"
        reserved = _reserve_output_path(str(target))
        self.assertEqual(reserved, target)
        self.assertTrue(target.parent.is_dir())


class EmitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_returns_actual_saved_path(self) -> None:
        target = self.base / "out.csv"
        with quiet():
            first = _emit("A\n", str(target))
            second = _emit("B\n", str(target))

        self.assertEqual(first, target)
        self.assertNotEqual(second, target)
        # 두 번째 호출이 첫 파일을 건드리지 않았는지
        self.assertEqual(first.read_text(encoding="utf-8"), "A\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B\n")

    def test_stdout_mode_returns_none(self) -> None:
        with quiet() as (out, _):
            self.assertIsNone(_emit("표 내용\n", None))
        self.assertEqual(out.getvalue(), "표 내용\n")

    def test_unwritable_path_exits_with_code_2(self) -> None:
        # 파일을 디렉터리처럼 쓰려 하면 OSError → SystemExit(2) 로 정리해서 노출
        blocker = self.base / "blocker"
        blocker.write_text("x", encoding="utf-8")
        with quiet() as (_, err), self.assertRaises(SystemExit) as ctx:
            _emit("내용", str(blocker / "child.csv"))
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("저장 실패", err.getvalue())


if __name__ == "__main__":
    unittest.main()
