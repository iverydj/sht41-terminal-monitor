from __future__ import annotations

import zipfile
from pathlib import Path


class BuildConfig:
    ROOT = Path(__file__).resolve().parent
    ARCHIVE_DIRECTORY = Path("SHT41_Monitor_Ubuntu")
    RELEASE_DIRECTORY = ROOT / "release"
    ZIP_PATH = RELEASE_DIRECTORY / "SHT41_Monitor_Ubuntu_26.04_v2.1.0.zip"
    FILES = (
        (ROOT / "sht41_monitor.py", "sht41_monitor.py", False),
        (ROOT / "ubuntu" / "install.sh", "install.sh", True),
        (ROOT / "ubuntu" / "sht41-monitor", "sht41-monitor", True),
        (
            ROOT / "ubuntu" / "sht41-logger.service.in",
            "sht41-logger.service.in",
            False,
        ),
        (ROOT / "ubuntu" / "README.txt", "README.txt", False),
        (ROOT / "LICENSE", "LICENSE", False),
    )


def _write_file(
    archive: zipfile.ZipFile, source: Path, destination: Path, executable: bool
) -> None:
    info = zipfile.ZipInfo.from_file(source, destination.as_posix())
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((0o100755 if executable else 0o100644) & 0xFFFF) << 16
    archive.writestr(info, source.read_bytes(), compresslevel=9)


def main() -> None:
    BuildConfig.RELEASE_DIRECTORY.mkdir(parents=True, exist_ok=True)

    for source, name, _ in BuildConfig.FILES:
        if not source.is_file():
            raise RuntimeError(f"배포 파일이 없습니다: {source}")

    with zipfile.ZipFile(BuildConfig.ZIP_PATH, mode="w") as archive:
        for source, name, executable in BuildConfig.FILES:
            _write_file(
                archive,
                source,
                BuildConfig.ARCHIVE_DIRECTORY / name,
                executable,
            )

    print(f"완료: {BuildConfig.ZIP_PATH}")


if __name__ == "__main__":
    main()
