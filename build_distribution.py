from __future__ import annotations

import os
import shutil
import stat
import struct
import subprocess
import sys
import zipfile
from pathlib import Path


class BuildConfig:
    ROOT = Path(__file__).resolve().parent
    BUILD_ROOT = ROOT / "build" / "distribution"
    PYINSTALLER_WORK = BUILD_ROOT / "work"
    PYINSTALLER_SPEC = BUILD_ROOT / "spec"
    PYINSTALLER_DIST = BUILD_ROOT / "dist"
    STAGING_DIRECTORY = BUILD_ROOT / "SHT41 Monitor"
    RELEASE_DIRECTORY = ROOT / "release"
    ZIP_PATH = RELEASE_DIRECTORY / "SHT41_Monitor_Windows_x64_v2.zip"
    EXECUTABLE_NAMES = ("SHT41 Monitor.exe", "SHT41 Logger.exe")


def _remove_owned_directory(path: Path) -> None:
    resolved_root = BuildConfig.ROOT.resolve()
    resolved_path = path.resolve()
    if resolved_root not in resolved_path.parents:
        raise RuntimeError(f"빌드 폴더 밖은 삭제할 수 없습니다: {resolved_path}")
    if path.exists():
        def remove_readonly(function, target, _error):
            os.chmod(target, stat.S_IWRITE)
            function(target)

        shutil.rmtree(path, onexc=remove_readonly)


def _build_executable(name: str, console: bool) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onefile",
            "--console" if console else "--noconsole",
            "--name",
            name,
            "--workpath",
            str(BuildConfig.PYINSTALLER_WORK),
            "--specpath",
            str(BuildConfig.PYINSTALLER_SPEC),
            "--distpath",
            str(BuildConfig.PYINSTALLER_DIST),
            str(BuildConfig.ROOT / "sht41_monitor.py"),
        ],
        check=True,
        cwd=BuildConfig.ROOT,
    )


def main() -> None:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Windows 64비트 환경에서만 배포 파일을 만들 수 있습니다.")

    _remove_owned_directory(BuildConfig.BUILD_ROOT)
    BuildConfig.RELEASE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if BuildConfig.ZIP_PATH.exists():
        BuildConfig.ZIP_PATH.unlink()

    _build_executable("SHT41 Monitor", console=True)
    _build_executable("SHT41 Logger", console=False)

    BuildConfig.STAGING_DIRECTORY.mkdir(parents=True)
    for executable_name in BuildConfig.EXECUTABLE_NAMES:
        shutil.copy2(
            BuildConfig.PYINSTALLER_DIST / executable_name,
            BuildConfig.STAGING_DIRECTORY / executable_name,
        )
    shutil.copy2(
        BuildConfig.ROOT / "distribution_readme.txt",
        BuildConfig.STAGING_DIRECTORY / "README.txt",
    )

    with zipfile.ZipFile(
        BuildConfig.ZIP_PATH,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for file_path in sorted(BuildConfig.STAGING_DIRECTORY.iterdir()):
            archive.write(
                file_path,
                arcname=Path(BuildConfig.STAGING_DIRECTORY.name) / file_path.name,
            )

    print(f"완료: {BuildConfig.ZIP_PATH}")


if __name__ == "__main__":
    main()
