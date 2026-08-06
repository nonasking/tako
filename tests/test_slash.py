"""슬래시 커맨드 설치 단위 테스트 (임시 디렉터리에만 씀).

PyPI 로 설치한 사용자는 저장소가 없다 — 커맨드 파일이 패키지에 동봉돼 있고
`tako slash install` 로 꺼낼 수 있어야 슬래시 모드가 살아남는다.

실행: python -m unittest tests.test_slash
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tako.slash import SlashError, install_commands, packaged_commands, resolve_commands_dir


class PackagedTest(unittest.TestCase):
    def test_commands_ship_with_the_package(self) -> None:
        entries = packaged_commands()
        names = [name for name, _ in entries]
        self.assertIn("tako.md", names)
        # 슬래시 모드의 커맨드 일습이 전부 들어 있어야 한다.
        for expected in ("tako-read.md", "tako-check.md", "tako-update.md",
                         "tako-retype.md", "tako-list.md", "tako-guide.md"):
            self.assertIn(expected, names)

    def test_contents_are_not_empty(self) -> None:
        for name, text in packaged_commands():
            with self.subTest(name=name):
                self.assertTrue(text.strip(), f"{name} 이 비어 있음")


class InstallTest(unittest.TestCase):
    def test_writes_every_command(self) -> None:
        with TemporaryDirectory() as tmp:
            written, skipped = install_commands(tmp)
            self.assertEqual(skipped, [])
            self.assertEqual(len(written), len(packaged_commands()))
            for name in written:
                self.assertTrue((Path(tmp) / name).is_file())

    def test_creates_missing_parent_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b" / "commands"
            install_commands(nested)
            self.assertTrue((nested / "tako.md").is_file())

    def test_second_run_skips_instead_of_overwriting(self) -> None:
        # 사용자가 손본 커맨드를 조용히 날리면 안 된다.
        with TemporaryDirectory() as tmp:
            install_commands(tmp)
            edited = Path(tmp) / "tako.md"
            edited.write_text("내가 고친 내용", encoding="utf-8")

            written, skipped = install_commands(tmp)
            self.assertEqual(written, [])
            self.assertIn("tako.md", skipped)
            self.assertEqual(edited.read_text(encoding="utf-8"), "내가 고친 내용")

    def test_force_overwrites(self) -> None:
        with TemporaryDirectory() as tmp:
            install_commands(tmp)
            edited = Path(tmp) / "tako.md"
            edited.write_text("내가 고친 내용", encoding="utf-8")

            written, skipped = install_commands(tmp, force=True)
            self.assertIn("tako.md", written)
            self.assertEqual(skipped, [])
            self.assertNotEqual(edited.read_text(encoding="utf-8"), "내가 고친 내용")

    def test_replaces_a_dangling_symlink_from_the_old_installer(self) -> None:
        # 예전 install.sh 는 저장소로 심볼릭 링크를 걸었다 — 저장소가 사라지면 끊긴 링크가 남는다.
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "tako.md"
            dest.symlink_to(Path(tmp) / "does-not-exist.md")
            self.assertTrue(dest.is_symlink())

            written, _ = install_commands(tmp, force=True)
            self.assertIn("tako.md", written)
            self.assertFalse(dest.is_symlink())
            self.assertTrue(dest.is_file())


class ResolveTest(unittest.TestCase):
    def test_explicit_path_wins(self) -> None:
        self.assertEqual(resolve_commands_dir("/tmp/x"), Path("/tmp/x"))

    def test_env_override(self) -> None:
        import os
        prev = os.environ.get("TAKO_COMMANDS_DIR")
        os.environ["TAKO_COMMANDS_DIR"] = "/tmp/from-env"
        try:
            self.assertEqual(resolve_commands_dir(), Path("/tmp/from-env"))
        finally:
            if prev is None:
                del os.environ["TAKO_COMMANDS_DIR"]
            else:
                os.environ["TAKO_COMMANDS_DIR"] = prev

    def test_default_is_under_claude_commands(self) -> None:
        import os
        prev = os.environ.pop("TAKO_COMMANDS_DIR", None)
        try:
            self.assertEqual(resolve_commands_dir().parts[-2:], (".claude", "commands"))
        finally:
            if prev is not None:
                os.environ["TAKO_COMMANDS_DIR"] = prev


class ErrorTest(unittest.TestCase):
    def test_unwritable_target_raises_slash_error(self) -> None:
        with TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocked"
            blocker.write_text("나는 파일이다", encoding="utf-8")
            with self.assertRaises(SlashError):
                install_commands(blocker / "commands")


if __name__ == "__main__":
    unittest.main()
