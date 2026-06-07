"""tako.guide 단위 테스트 (stdlib unittest — 추가 의존성 없음).

실행: python -m unittest tests.test_guide
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tako import guide


class GuideTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.guide_path = Path(self._tmp.name) / "body_guide.md"
        self._prev_env = os.environ.get(guide.ENV_OVERRIDE_VAR)
        os.environ[guide.ENV_OVERRIDE_VAR] = str(self.guide_path)

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop(guide.ENV_OVERRIDE_VAR, None)
        else:
            os.environ[guide.ENV_OVERRIDE_VAR] = self._prev_env
        self._tmp.cleanup()

    def test_default_guide_text_packaged(self) -> None:
        text = guide.default_guide_text()
        self.assertTrue(text.strip())
        self.assertIn("자가 점검", text)  # 동봉 기본 가이드의 핵심 섹션

    def test_resolve_path_env_override(self) -> None:
        self.assertEqual(guide.resolve_guide_path(), self.guide_path)
        explicit = Path(self._tmp.name) / "other.md"
        self.assertEqual(guide.resolve_guide_path(explicit), explicit)

    def test_effective_default_when_absent(self) -> None:
        text, source = guide.effective_guide()
        self.assertEqual(source, "default")
        self.assertEqual(text, guide.default_guide_text())

    def test_init_creates_then_personal(self) -> None:
        target = guide.write_default_guide()
        self.assertEqual(target, self.guide_path)
        self.assertTrue(self.guide_path.exists())

        text, source = guide.effective_guide()
        self.assertEqual(source, "personal")
        self.assertEqual(text, guide.default_guide_text())

    def test_init_refuses_existing_without_force(self) -> None:
        guide.write_default_guide()
        with self.assertRaises(guide.GuideError):
            guide.write_default_guide()

    def test_reset_overwrites_with_force(self) -> None:
        self.guide_path.write_text("내 커스텀 가이드\n", encoding="utf-8")
        text, source = guide.effective_guide()
        self.assertEqual(source, "personal")
        self.assertEqual(text, "내 커스텀 가이드\n")

        guide.write_default_guide(force=True)
        text2, _ = guide.effective_guide()
        self.assertEqual(text2, guide.default_guide_text())


if __name__ == "__main__":
    unittest.main()
