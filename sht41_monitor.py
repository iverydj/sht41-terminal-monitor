from __future__ import annotations

import csv
import ctypes
import hashlib
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


APPLICATION_DIRECTORY = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


class AppConfig:
    """사용자가 바꿀 수 있는 설정. 명령행 옵션은 사용하지 않는다."""

    SERIAL_PORT: str | None = None  # 예: "COM4"; None이면 자동 검색
    BAUD_RATE = 115_200
    SERIAL_TIMEOUT_SECONDS = 3.0
    PROBE_TIMEOUT_SECONDS = 5.0

    DISPLAY_SAMPLE_SECONDS = 10
    PERSIST_BUCKET_SECONDS = 5 * 60
    PANELS = (
        ("최근 10분", 10 * 60),
        ("최근 1시간", 60 * 60),
        ("최근 1일", 24 * 60 * 60),
        ("최근 1주", 7 * 24 * 60 * 60),
    )

    DATA_DIRECTORY = APPLICATION_DIRECTORY / "data"
    EXPORT_DIRECTORY = APPLICATION_DIRECTORY / "exports"
    LOGGER_ERROR_PATH = APPLICATION_DIRECTORY / "sht41_logger_error.txt"
    SENSOR_SCAN_SECONDS = 2
    MAX_SENSOR_NAME_WIDTH = 30
    MIN_TERMINAL_COLUMNS = 120
    MIN_TERMINAL_LINES = 42


ANSI_RESET = "\x1b[0m"
ANSI_RED = "\x1b[91m"
ANSI_CYAN = "\x1b[96m"
ANSI_MAGENTA = "\x1b[95m"
ANSI_DIM = "\x1b[2m"


@dataclass(frozen=True)
class SensorReading:
    timestamp: int
    sensor_serial: int
    temperature_c: float
    humidity_rh: float


@dataclass(frozen=True)
class PlotPoint:
    timestamp: int
    temperature_c: float
    humidity_rh: float
    sample_count: int = 1


@dataclass(frozen=True)
class RuntimeStatus:
    state: str
    updated_utc: int
    sensor_serial: int | None
    port_name: str | None
    temperature_c: float | None
    humidity_rh: float | None
    message: str


@dataclass(frozen=True)
class SensorSummary:
    sensor_serial: int
    display_name: str
    database_path: Path
    status: RuntimeStatus


def sensor_database_path(
    sensor_serial: int, data_directory: Path | None = None
) -> Path:
    root = AppConfig.DATA_DIRECTORY if data_directory is None else data_directory
    return root / f"sensor_{sensor_serial}" / "sht41_history.sqlite3"


def sensor_export_directory(sensor_serial: int) -> Path:
    return AppConfig.EXPORT_DIRECTORY / f"sensor_{sensor_serial}"


def logger_stop_request_path(data_directory: Path | None = None) -> Path:
    root = AppConfig.DATA_DIRECTORY if data_directory is None else data_directory
    return root / "stop_logger.request"


def parse_factory_line(line: str, timestamp: int | None = None) -> SensorReading:
    """Adafruit 출고 펌웨어의 CSV 한 줄을 엄격하게 해석한다."""
    parts = [part.strip() for part in line.strip().split(",")]
    if len(parts) != 4:
        raise ValueError("expected four CSV fields")

    sensor_serial = int(parts[0])
    temperature_c = float(parts[1])
    humidity_rh = float(parts[2])
    int(parts[3])  # 터치값은 형식 검증만 하고 기록하지 않는다.

    if sensor_serial <= 0:
        raise ValueError("invalid sensor serial")
    if not math.isfinite(temperature_c) or not -40.0 <= temperature_c <= 125.0:
        raise ValueError("temperature outside SHT41 range")
    if not math.isfinite(humidity_rh) or not 0.0 <= humidity_rh <= 100.0:
        raise ValueError("humidity outside SHT41 range")

    return SensorReading(
        timestamp=int(time.time()) if timestamp is None else timestamp,
        sensor_serial=sensor_serial,
        temperature_c=temperature_c,
        humidity_rh=humidity_rh,
    )


class HistoryDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=5.0)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS readings_5m (
                bucket_utc INTEGER PRIMARY KEY,
                sample_count INTEGER NOT NULL CHECK (sample_count > 0),
                temp_sum_centi INTEGER NOT NULL,
                temp_min_centi INTEGER NOT NULL,
                temp_max_centi INTEGER NOT NULL,
                humidity_sum_centi INTEGER NOT NULL,
                humidity_min_centi INTEGER NOT NULL,
                humidity_max_centi INTEGER NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS recent_readings (
                timestamp_utc INTEGER PRIMARY KEY,
                temp_centi INTEGER NOT NULL,
                humidity_centi INTEGER NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS runtime_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                state TEXT NOT NULL,
                updated_utc INTEGER NOT NULL,
                sensor_serial INTEGER,
                port_name TEXT,
                temp_centi INTEGER,
                humidity_centi INTEGER,
                message TEXT NOT NULL,
                stop_requested INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.connection.commit()

    def bind_sensor(self, sensor_serial: int) -> None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'sensor_serial'"
        ).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('sensor_serial', ?)",
                (str(sensor_serial),),
            )
            self.connection.commit()
            return
        if int(row[0]) != sensor_serial:
            raise RuntimeError(
                f"기록 파일은 센서 {row[0]} 전용인데 현재 센서는 {sensor_serial}입니다."
            )

    def display_name(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'display_name'"
        ).fetchone()
        return "이름 없음" if row is None else str(row[0])

    def set_display_name(self, name: str) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES('display_name', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (name,),
        )
        self.connection.commit()

    def record(self, reading: SensorReading) -> None:
        bucket = (
            reading.timestamp
            - reading.timestamp % AppConfig.PERSIST_BUCKET_SECONDS
        )
        temp = int(round(reading.temperature_c * 100))
        humidity = int(round(reading.humidity_rh * 100))
        self.connection.execute(
            """
            INSERT INTO readings_5m(
                bucket_utc, sample_count,
                temp_sum_centi, temp_min_centi, temp_max_centi,
                humidity_sum_centi, humidity_min_centi, humidity_max_centi
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bucket_utc) DO UPDATE SET
                sample_count = sample_count + 1,
                temp_sum_centi = temp_sum_centi + excluded.temp_sum_centi,
                temp_min_centi = MIN(temp_min_centi, excluded.temp_min_centi),
                temp_max_centi = MAX(temp_max_centi, excluded.temp_max_centi),
                humidity_sum_centi =
                    humidity_sum_centi + excluded.humidity_sum_centi,
                humidity_min_centi =
                    MIN(humidity_min_centi, excluded.humidity_min_centi),
                humidity_max_centi =
                    MAX(humidity_max_centi, excluded.humidity_max_centi)
            """,
            (bucket, temp, temp, temp, humidity, humidity, humidity),
        )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO recent_readings(
                timestamp_utc, temp_centi, humidity_centi
            ) VALUES (?, ?, ?)
            """,
            (reading.timestamp, temp, humidity),
        )
        self.connection.execute(
            "DELETE FROM recent_readings WHERE timestamp_utc < ?",
            (reading.timestamp - max(span for _, span in AppConfig.PANELS),),
        )
        self.connection.commit()

    def publish_running(self, reading: SensorReading, port_name: str) -> None:
        self._set_runtime_state(
            state="running",
            updated_utc=reading.timestamp,
            sensor_serial=reading.sensor_serial,
            port_name=port_name,
            temperature_c=reading.temperature_c,
            humidity_rh=reading.humidity_rh,
            message="정상적으로 기록 중입니다.",
        )

    def publish_state(self, state: str, message: str) -> None:
        self._set_runtime_state(
            state=state,
            updated_utc=int(time.time()),
            sensor_serial=None,
            port_name=None,
            temperature_c=None,
            humidity_rh=None,
            message=message,
        )

    def _set_runtime_state(
        self,
        state: str,
        updated_utc: int,
        sensor_serial: int | None,
        port_name: str | None,
        temperature_c: float | None,
        humidity_rh: float | None,
        message: str,
    ) -> None:
        temp = None if temperature_c is None else int(round(temperature_c * 100))
        humidity = None if humidity_rh is None else int(round(humidity_rh * 100))
        self.connection.execute(
            """
            INSERT INTO runtime_state(
                singleton, state, updated_utc, sensor_serial, port_name,
                temp_centi, humidity_centi, message, stop_requested
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(singleton) DO UPDATE SET
                state = excluded.state,
                updated_utc = excluded.updated_utc,
                sensor_serial = COALESCE(
                    excluded.sensor_serial, runtime_state.sensor_serial
                ),
                port_name = COALESCE(excluded.port_name, runtime_state.port_name),
                temp_centi = COALESCE(excluded.temp_centi, runtime_state.temp_centi),
                humidity_centi = COALESCE(
                    excluded.humidity_centi, runtime_state.humidity_centi
                ),
                message = excluded.message
            """,
            (
                state,
                updated_utc,
                sensor_serial,
                port_name,
                temp,
                humidity,
                message,
            ),
        )
        self.connection.commit()

    def load_runtime_status(self) -> RuntimeStatus | None:
        row = self.connection.execute(
            """
            SELECT state, updated_utc, sensor_serial, port_name,
                   temp_centi, humidity_centi, message
            FROM runtime_state
            WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            return None
        return RuntimeStatus(
            state=str(row[0]),
            updated_utc=int(row[1]),
            sensor_serial=None if row[2] is None else int(row[2]),
            port_name=None if row[3] is None else str(row[3]),
            temperature_c=None if row[4] is None else int(row[4]) / 100.0,
            humidity_rh=None if row[5] is None else int(row[5]) / 100.0,
            message=str(row[6]),
        )

    def request_stop(self) -> None:
        self.connection.execute(
            "UPDATE runtime_state SET stop_requested = 1 WHERE singleton = 1"
        )
        self.connection.commit()

    def clear_stop_request(self) -> None:
        self.connection.execute(
            "UPDATE runtime_state SET stop_requested = 0 WHERE singleton = 1"
        )
        self.connection.commit()

    def stop_requested(self) -> bool:
        row = self.connection.execute(
            "SELECT stop_requested FROM runtime_state WHERE singleton = 1"
        ).fetchone()
        return row is not None and bool(row[0])

    def load_plot_points(self, since_timestamp: int) -> list[PlotPoint]:
        rows = self.connection.execute(
            """
            SELECT
                bucket_utc,
                temp_sum_centi * 1.0 / sample_count / 100.0,
                humidity_sum_centi * 1.0 / sample_count / 100.0,
                sample_count
            FROM readings_5m
            WHERE bucket_utc >= ?
            ORDER BY bucket_utc
            """,
            (since_timestamp,),
        )
        return [
            PlotPoint(
                timestamp=int(row[0]),
                temperature_c=float(row[1]),
                humidity_rh=float(row[2]),
                sample_count=int(row[3]),
            )
            for row in rows
        ]

    def load_dashboard_points(self, since_timestamp: int) -> list[PlotPoint]:
        recent_rows = list(
            self.connection.execute(
                """
                SELECT timestamp_utc, temp_centi / 100.0, humidity_centi / 100.0
                FROM recent_readings
                WHERE timestamp_utc >= ?
                ORDER BY timestamp_utc
                """,
                (since_timestamp,),
            )
        )
        recent = [
            PlotPoint(int(row[0]), float(row[1]), float(row[2]))
            for row in recent_rows
        ]
        if not recent:
            return self.load_plot_points(since_timestamp)
        recent_bucket = (
            recent[0].timestamp
            - recent[0].timestamp % AppConfig.PERSIST_BUCKET_SECONDS
        )
        older = [
            point
            for point in self.load_plot_points(since_timestamp)
            if point.timestamp < recent_bucket
        ]
        return older + recent

    def aggregate_for_bucket(self, bucket: int) -> tuple[int, ...] | None:
        return self.connection.execute(
            """
            SELECT sample_count, temp_sum_centi, temp_min_centi, temp_max_centi,
                   humidity_sum_centi, humidity_min_centi, humidity_max_centi
            FROM readings_5m
            WHERE bucket_utc = ?
            """,
            (bucket,),
        ).fetchone()

    def close(self) -> None:
        self.connection.close()


def migrate_legacy_database(data_directory: Path | None = None) -> Path | None:
    root = AppConfig.DATA_DIRECTORY if data_directory is None else data_directory
    legacy_path = root / "sht41_history.sqlite3"
    if not legacy_path.exists():
        return None

    sidecars = [
        Path(str(legacy_path) + suffix)
        for suffix in ("-journal", "-wal", "-shm")
        if Path(str(legacy_path) + suffix).exists()
    ]
    if sidecars:
        raise RuntimeError(
            "기존 기록 파일이 사용 중입니다. 이전 Logger를 완전히 종료한 뒤 "
            "다시 실행하세요."
        )

    connection = sqlite3.connect(
        legacy_path.resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'sensor_serial'"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("기존 기록 파일에서 센서 ID를 찾지 못했습니다.")

    destination = sensor_database_path(int(row[0]), root)
    if destination.exists():
        raise RuntimeError(
            f"기존 기록과 새 센서별 기록이 모두 존재합니다: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.replace(destination)
    return destination


def export_history_csv(
    database: HistoryDatabase,
    export_directory: Path,
    exported_at: int | None = None,
) -> tuple[Path, int]:
    """장기 5분 기록 전체를 Excel에서 읽기 쉬운 CSV로 내보낸다."""
    export_directory.mkdir(parents=True, exist_ok=True)
    export_time = int(time.time()) if exported_at is None else exported_at
    timestamp = datetime.fromtimestamp(export_time).strftime("%Y%m%d_%H%M%S")
    destination = export_directory / f"SHT41_기록_{timestamp}.csv"
    suffix = 2
    while destination.exists():
        destination = export_directory / f"SHT41_기록_{timestamp}_{suffix}.csv"
        suffix += 1

    latest_row = database.connection.execute(
        "SELECT MAX(bucket_utc) FROM readings_5m"
    ).fetchone()
    latest_bucket = None if latest_row is None else latest_row[0]
    sensor_row = database.connection.execute(
        "SELECT value FROM metadata WHERE key = 'sensor_serial'"
    ).fetchone()
    if sensor_row is None:
        raise RuntimeError("기록 파일에서 센서 ID를 찾지 못했습니다.")
    sensor_serial = int(sensor_row[0])
    display_name = database.display_name()
    fieldnames = (
        "센서 이름",
        "센서 ID",
        "5분 구간 시작 (PC 시간)",
        "5분 구간 시작 (UTC)",
        "평균 온도 (°C)",
        "최저 온도 (°C)",
        "최고 온도 (°C)",
        "평균 습도 (%RH)",
        "최저 습도 (%RH)",
        "최고 습도 (%RH)",
        "측정 횟수",
    )
    row_count = 0
    last_bucket = -1
    with destination.open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(fieldnames)
        while latest_bucket is not None:
            rows = database.connection.execute(
                """
                SELECT bucket_utc, sample_count,
                       temp_sum_centi, temp_min_centi, temp_max_centi,
                       humidity_sum_centi, humidity_min_centi,
                       humidity_max_centi
                FROM readings_5m
                WHERE bucket_utc > ? AND bucket_utc <= ?
                ORDER BY bucket_utc
                LIMIT 10000
                """,
                (last_bucket, latest_bucket),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                (
                    bucket,
                    sample_count,
                    temp_sum,
                    temp_min,
                    temp_max,
                    humidity_sum,
                    humidity_min,
                    humidity_max,
                ) = row
                utc_datetime = datetime.fromtimestamp(bucket, timezone.utc)
                local_time = utc_datetime.astimezone().isoformat(timespec="seconds")
                utc_time = utc_datetime.isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                )
                writer.writerow(
                    (
                        display_name,
                        sensor_serial,
                        local_time,
                        utc_time,
                        f"{temp_sum / sample_count / 100:.2f}",
                        f"{temp_min / 100:.2f}",
                        f"{temp_max / 100:.2f}",
                        f"{humidity_sum / sample_count / 100:.2f}",
                        f"{humidity_min / 100:.2f}",
                        f"{humidity_max / 100:.2f}",
                        sample_count,
                    )
                )
                row_count += 1
            last_bucket = rows[-1][0]
    return destination, row_count


class SHT41Serial:
    def __init__(self, device, port_name: str, first_reading: SensorReading):
        self.device = device
        self.port_name = port_name
        self.first_reading = first_reading

    @staticmethod
    def _serial_modules():
        try:
            import serial
            from serial.tools import list_ports
        except ImportError as exc:
            raise RuntimeError(
                "pyserial이 없습니다. setup.cmd를 먼저 실행하세요."
            ) from exc
        return serial, list_ports

    @classmethod
    def candidate_ports(cls) -> list[str]:
        _, list_ports = cls._serial_modules()
        ports = sorted(list_ports.comports())
        if AppConfig.SERIAL_PORT is not None:
            return [AppConfig.SERIAL_PORT]
        adafruit = [
            port.device
            for port in ports
            if port.vid == 0x239A
            or "adafruit" in (port.description or "").lower()
            or "trinkey" in (port.description or "").lower()
        ]
        usb_serial = [
            port.device
            for port in ports
            if port.vid is not None and port.device not in adafruit
        ]
        return adafruit or usb_serial

    @classmethod
    def connect_port(cls, port_name: str) -> "SHT41Serial":
        serial, _ = cls._serial_modules()
        device = None
        try:
            device = serial.Serial(
                port=port_name,
                baudrate=AppConfig.BAUD_RATE,
                timeout=AppConfig.SERIAL_TIMEOUT_SECONDS,
            )
            device.dtr = True
            device.rts = True
            deadline = time.monotonic() + AppConfig.PROBE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                raw = device.readline()
                if not raw:
                    continue
                try:
                    reading = parse_factory_line(raw.decode("utf-8").strip())
                except (UnicodeDecodeError, ValueError):
                    continue
                return cls(device, port_name, reading)
            raise RuntimeError(f"{port_name}: SHT41 데이터 없음")
        except serial.SerialException as exc:
            if device is not None and device.is_open:
                device.close()
            raise RuntimeError(f"{port_name}: {exc}") from exc
        except Exception:
            if device is not None and device.is_open:
                device.close()
            raise

    @classmethod
    def connect(cls) -> "SHT41Serial":
        candidates = cls.candidate_ports()
        if not candidates:
            raise RuntimeError("SHT41 후보 USB 시리얼 포트를 찾지 못했습니다.")
        failures = []
        for port_name in candidates:
            try:
                return cls.connect_port(port_name)
            except RuntimeError as exc:
                failures.append(str(exc))
        raise RuntimeError("SHT41 연결에 실패했습니다. " + "; ".join(failures))

    def read(self) -> SensorReading:
        while True:
            raw = self.device.readline()
            if not raw:
                raise RuntimeError(
                    f"{self.port_name}에서 {AppConfig.SERIAL_TIMEOUT_SECONDS}초 동안 "
                    "데이터가 오지 않았습니다."
                )
            try:
                return parse_factory_line(raw.decode("utf-8").strip())
            except (UnicodeDecodeError, ValueError):
                continue

    def close(self) -> None:
        if self.device.is_open:
            self.device.close()


BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def _value_range(values: Sequence[float], minimum_padding: float) -> tuple[float, float]:
    low = min(values)
    high = max(values)
    if high == low:
        return low - minimum_padding, high + minimum_padding
    padding = (high - low) * 0.05
    return low - padding, high + padding


def _draw_line(
    masks: list[list[int]],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        row_from_top = len(masks) * 4 - 1 - y0
        cell_y, dot_y = divmod(row_from_top, 4)
        cell_x, dot_x = divmod(x0, 2)
        masks[cell_y][cell_x] |= BRAILLE_BITS[dot_y][dot_x]
        if x0 == x1 and y0 == y1:
            return
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def _series_masks(
    points: Sequence[PlotPoint],
    values: Sequence[float],
    start_timestamp: int,
    end_timestamp: int,
    width: int,
    height: int,
    value_low: float,
    value_high: float,
) -> list[list[int]]:
    masks = [[0 for _ in range(width)] for _ in range(height)]
    sub_width = width * 2
    sub_height = height * 4
    previous: tuple[int, int, int] | None = None
    for point, value in zip(points, values, strict=True):
        x = round(
            (point.timestamp - start_timestamp)
            / max(1, end_timestamp - start_timestamp)
            * (sub_width - 1)
        )
        y = round((value - value_low) / (value_high - value_low) * (sub_height - 1))
        x = min(max(x, 0), sub_width - 1)
        y = min(max(y, 0), sub_height - 1)
        if previous is None or point.timestamp - previous[2] > 15 * 60:
            _draw_line(masks, x, y, x, y)
        else:
            _draw_line(masks, previous[0], previous[1], x, y)
        previous = (x, y, point.timestamp)
    return masks


def braille_graph(
    points: Sequence[PlotPoint],
    start_timestamp: int,
    end_timestamp: int,
    width: int,
    height: int,
    use_color: bool = True,
) -> tuple[list[str], tuple[float, float], tuple[float, float]]:
    if not points:
        empty = [" " * width for _ in range(height)]
        return empty, (math.nan, math.nan), (math.nan, math.nan)

    temperatures = [point.temperature_c for point in points]
    humidities = [point.humidity_rh for point in points]
    temp_range = _value_range(temperatures, 0.1)
    humidity_range = _value_range(humidities, 0.5)
    temp_masks = _series_masks(
        points,
        temperatures,
        start_timestamp,
        end_timestamp,
        width,
        height,
        *temp_range,
    )
    humidity_masks = _series_masks(
        points,
        humidities,
        start_timestamp,
        end_timestamp,
        width,
        height,
        *humidity_range,
    )

    lines: list[str] = []
    for row in range(height):
        cells: list[str] = []
        active_color = ""
        for column in range(width):
            temp_mask = temp_masks[row][column]
            humidity_mask = humidity_masks[row][column]
            mask = temp_mask | humidity_mask
            if temp_mask and humidity_mask:
                color = ANSI_MAGENTA
            elif temp_mask:
                color = ANSI_RED
            elif humidity_mask:
                color = ANSI_CYAN
            else:
                color = ""
            if use_color and color != active_color:
                cells.append(color or ANSI_RESET)
                active_color = color
            cells.append(chr(0x2800 + mask) if mask else " ")
        if use_color and active_color:
            cells.append(ANSI_RESET)
        lines.append("".join(cells))
    return lines, temp_range, humidity_range


def _display_width(text: str) -> int:
    width = 0
    for character in text:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def _fit(text: str, width: int, align: str = "left") -> str:
    result: list[str] = []
    used = 0
    for character in text:
        character_width = _display_width(character)
        if used + character_width > width:
            break
        result.append(character)
        used += character_width
    padding = " " * (width - used)
    if align == "right":
        return padding + "".join(result)
    if align == "center":
        left = len(padding) // 2
        return " " * left + "".join(result) + " " * (len(padding) - left)
    return "".join(result) + padding


BIG_DIGITS = {
    "0": ("█████", "█   █", "█   █", "█   █", "█████"),
    "1": ("  ██ ", " ███ ", "  ██ ", "  ██ ", "█████"),
    "2": ("█████", "    █", "█████", "█    ", "█████"),
    "3": ("█████", "    █", " ████", "    █", "█████"),
    "4": ("█   █", "█   █", "█████", "    █", "    █"),
    "5": ("█████", "█    ", "█████", "    █", "█████"),
    "6": ("█████", "█    ", "█████", "█   █", "█████"),
    "7": ("█████", "    █", "   █ ", "  █  ", "  █  "),
    "8": ("█████", "█   █", "█████", "█   █", "█████"),
    "9": ("█████", "█   █", "█████", "    █", "█████"),
    ".": ("  ", "  ", "  ", "  ", "██"),
    "-": ("     ", "     ", "█████", "     ", "     "),
}


def _big_number(value: float) -> list[str]:
    text = f"{value:.2f}"
    return [
        " ".join(BIG_DIGITS[character][row] for character in text)
        for row in range(5)
    ]


def make_current_header(
    temperature_c: float,
    humidity_rh: float,
    measured_at: int,
    width: int,
    use_color: bool,
    sensor_title: str | None = None,
) -> list[str]:
    left_width = (width - 3) // 2
    right_width = width - 3 - left_width
    title_text = (
        "현재 측정값"
        if sensor_title is None
        else f"{sensor_title} · 현재 측정값"
    )
    title = "─ " + title_text + " "
    if _display_width(title) > width - 2:
        title = _fit(title, width - 2).rstrip()
    result = ["┌" + title + "─" * (width - 2 - _display_width(title)) + "┐"]
    result.append(
        "│"
        + _fit("현재 온도  (°C)", left_width, "center")
        + "│"
        + _fit("현재 상대습도  (%RH)", right_width, "center")
        + "│"
    )
    temperature_art = _big_number(temperature_c)
    humidity_art = _big_number(humidity_rh)
    for temperature_line, humidity_line in zip(
        temperature_art, humidity_art, strict=True
    ):
        left = _fit(temperature_line, left_width, "center")
        right = _fit(humidity_line, right_width, "center")
        if use_color:
            left = ANSI_RED + left + ANSI_RESET
            right = ANSI_CYAN + right + ANSI_RESET
        result.append("│" + left + "│" + right + "│")
    measured = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(measured_at))
    status = f"마지막 측정  {measured}   ·   10초마다 자동 갱신"
    result.append("│" + _fit(status, width - 2, "center") + "│")
    result.append("└" + "─" * (width - 2) + "┘")
    return result


def make_panel(
    label: str,
    span_seconds: int,
    all_points: Sequence[PlotPoint],
    now: int,
    width: int,
    height: int,
    use_color: bool = True,
) -> list[str]:
    inner_width = width - 2
    axis_width = 7
    graph_height = height - 5
    graph_width = inner_width - axis_width * 2
    start = now - span_seconds
    points = [point for point in all_points if start <= point.timestamp <= now]
    graph, temp_range, humidity_range = braille_graph(
        points, start, now, graph_width, graph_height, use_color
    )
    title = f"─ {label} "
    top = "┌" + title + "─" * (inner_width - _display_width(title)) + "┐"
    if points:
        total_samples = sum(point.sample_count for point in points)
        average_temperature = (
            sum(point.temperature_c * point.sample_count for point in points)
            / total_samples
        )
        average_humidity = (
            sum(point.humidity_rh * point.sample_count for point in points)
            / total_samples
        )
        summary = (
            f"기간 평균   온도 {average_temperature:.2f} °C   ·   "
            f"습도 {average_humidity:.2f} %RH   ·   측정 {total_samples}회"
        )
    else:
        summary = "기간 평균   아직 표시할 측정값이 없습니다."
        graph[graph_height // 2] = _fit("데이터 수집 중", graph_width, "center")

    result = [
        top,
        "│" + _fit(summary, inner_width, "center") + "│",
        "│"
        + _fit("°C", axis_width, "right")
        + " " * graph_width
        + _fit("%RH", axis_width)
        + "│",
    ]
    tick_rows = {0, graph_height // 2, graph_height - 1}
    for row, graph_line in enumerate(graph):
        if points and row in tick_rows:
            fraction = 1.0 - row / max(1, graph_height - 1)
            temperature_tick = temp_range[0] + fraction * (
                temp_range[1] - temp_range[0]
            )
            humidity_tick = humidity_range[0] + fraction * (
                humidity_range[1] - humidity_range[0]
            )
            left_label = f"{temperature_tick:5.1f} "
            right_label = f" {humidity_tick:5.1f}"
            left_mark, right_mark = "┤", "├"
        else:
            left_label = " " * (axis_width - 1)
            right_label = " " * (axis_width - 1)
            left_mark, right_mark = "│", "│"
        result.append(
            "│"
            + left_label
            + left_mark
            + graph_line
            + right_mark
            + right_label
            + "│"
        )
    past_label = label.removeprefix("최근 ") + " 전"
    time_axis = past_label + " " + "─" * max(
        1, graph_width - _display_width(past_label) - _display_width("현재") - 2
    ) + " 현재"
    result.append(
        "│"
        + " " * axis_width
        + _fit(time_axis, graph_width)
        + " " * axis_width
        + "│"
    )
    result.append("└" + "─" * inner_width + "┘")
    return result


def render_dashboard(
    status: RuntimeStatus,
    all_points: Sequence[PlotPoint],
    autostart_enabled: bool,
    sensor_name: str = "이름 없음",
    sensor_index: int = 0,
    sensor_count: int = 1,
    use_color: bool = True,
    notice: str | None = None,
) -> str:
    columns, lines = shutil.get_terminal_size((140, 46))
    if columns < AppConfig.MIN_TERMINAL_COLUMNS or lines < AppConfig.MIN_TERMINAL_LINES:
        raise RuntimeError(
            f"터미널이 너무 작습니다. 현재 {columns}×{lines}, 최소 "
            f"{AppConfig.MIN_TERMINAL_COLUMNS}×{AppConfig.MIN_TERMINAL_LINES}가 필요합니다."
        )

    if (
        status.sensor_serial is None
        or status.port_name is None
        or status.temperature_c is None
        or status.humidity_rh is None
    ):
        raise RuntimeError("백그라운드 기록기에서 현재 측정값을 받지 못했습니다.")

    now = int(time.time())
    header = make_current_header(
        status.temperature_c,
        status.humidity_rh,
        status.updated_utc,
        columns,
        use_color,
        sensor_title=(
            f"{sensor_name} · ID {status.sensor_serial} · {status.port_name}"
        ),
    )
    left_panel_width = (columns - 1) // 2
    right_panel_width = columns - 1 - left_panel_width
    panel_height = (lines - len(header) - 1) // 2
    panel_widths = (
        left_panel_width,
        right_panel_width,
        left_panel_width,
        right_panel_width,
    )
    panels = [
        make_panel(
            label,
            span,
            all_points,
            now,
            panel_width,
            panel_height,
            use_color,
        )
        for (label, span), panel_width in zip(
            AppConfig.PANELS, panel_widths, strict=True
        )
    ]
    rows = list(header)
    for left, right in ((panels[0], panels[1]), (panels[2], panels[3])):
        rows.extend(a + " " + b for a, b in zip(left, right, strict=True))
    startup = "켜짐" if autostart_enabled else "꺼짐"
    if notice is None:
        footer = (
            f"센서 {sensor_index + 1}/{sensor_count} · ←/→ 전환 · "
            f"N 이름 변경 · E CSV · 자동 시작 {startup} · "
            "Ctrl+C 화면 닫기 · Ctrl+Q 전체 기록기 종료"
        )
    else:
        footer = notice
    rows.append(
        (ANSI_DIM if use_color else "")
        + _fit(footer, columns)
        + (ANSI_RESET if use_color else "")
    )
    return "\n".join(rows)


def render_waiting_screen(
    sensor_name: str,
    sensor_serial: int,
    autostart_enabled: bool,
    use_color: bool = True,
) -> str:
    columns, lines = shutil.get_terminal_size((140, 46))
    if columns < AppConfig.MIN_TERMINAL_COLUMNS or lines < AppConfig.MIN_TERMINAL_LINES:
        raise RuntimeError(
            f"터미널이 너무 작습니다. 현재 {columns}×{lines}, 최소 "
            f"{AppConfig.MIN_TERMINAL_COLUMNS}×{AppConfig.MIN_TERMINAL_LINES}가 필요합니다."
        )

    rows = [" " * columns for _ in range(lines)]
    middle = lines // 2
    messages = (
        "SHT41 센서 재연결 대기 중",
        "",
        f"마지막 선택 센서  {sensor_name} · ID {sensor_serial}",
        "USB 연결을 확인하세요.",
        "센서가 다시 감지되면 저장과 모니터링을 자동으로 이어갑니다.",
    )
    for offset, message in enumerate(messages, start=-3):
        rows[middle + offset] = _fit(message, columns, "center")
    startup = "켜짐" if autostart_enabled else "꺼짐"
    footer = (
        f"재연결 자동 검색 중 · 자동 시작 {startup} · "
        "Ctrl+C 화면 닫기 · Ctrl+Q 전체 기록기 종료"
    )
    rows[-1] = (
        (ANSI_DIM if use_color else "")
        + _fit(footer, columns)
        + (ANSI_RESET if use_color else "")
    )
    return "\n".join(rows)


def _enable_windows_terminal() -> None:
    if os.name != "nt":
        return
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    kernel32.SetConsoleTitleW("SHT41 Temperature & Humidity Monitor")
    _resize_windows_console(handle, 140, 46)


def _resize_windows_console(handle, columns: int, lines: int) -> None:
    class Coord(ctypes.Structure):
        _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]

    class SmallRect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_short),
            ("top", ctypes.c_short),
            ("right", ctypes.c_short),
            ("bottom", ctypes.c_short),
        ]

    class ScreenBufferInfo(ctypes.Structure):
        _fields_ = [
            ("size", Coord),
            ("cursor_position", Coord),
            ("attributes", ctypes.c_ushort),
            ("window", SmallRect),
            ("maximum_window_size", Coord),
        ]

    kernel32 = ctypes.windll.kernel32
    info = ScreenBufferInfo()
    if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
        return
    buffer_size = Coord(max(columns, info.size.x), max(lines, info.size.y))
    kernel32.SetConsoleScreenBufferSize(handle, buffer_size)
    window = SmallRect(0, 0, columns - 1, lines - 1)
    kernel32.SetConsoleWindowInfo(handle, True, ctypes.byref(window))


class LoggerMutex:
    def __init__(self, handle):
        self.handle = handle

    @classmethod
    def acquire(cls) -> "LoggerMutex | None":
        if os.name != "nt":
            raise RuntimeError("백그라운드 기록기는 Windows에서만 지원합니다.")
        identity = hashlib.sha256(
            str(APPLICATION_DIRECTORY).lower().encode("utf-8")
        ).hexdigest()[:16]
        handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, f"Local\\SHT41MonitorLogger_{identity}"
        )
        if not handle:
            raise RuntimeError("백그라운드 기록기 잠금을 만들지 못했습니다.")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.kernel32.CloseHandle(handle)
            return None
        return cls(handle)

    def close(self) -> None:
        ctypes.windll.kernel32.CloseHandle(self.handle)


def _is_logger_mode() -> bool:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).stem.casefold() == "sht41 logger"
    return "--logger" in sys.argv[1:]


def _write_logger_error(message: str) -> None:
    try:
        AppConfig.LOGGER_ERROR_PATH.write_text(
            time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + message + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _run_sensor_connection(
    connection: SHT41Serial, stop_event: threading.Event
) -> None:
    sensor_serial = connection.first_reading.sensor_serial
    database = HistoryDatabase(sensor_database_path(sensor_serial))
    try:
        database.bind_sensor(sensor_serial)
        database.clear_stop_request()
        reading = connection.first_reading
        next_sample = time.monotonic()
        while not stop_event.is_set():
            if reading.sensor_serial != sensor_serial:
                raise RuntimeError(
                    f"{connection.port_name}의 센서 ID가 실행 중에 변경되었습니다."
                )
            if time.monotonic() >= next_sample:
                database.record(reading)
                database.publish_running(reading, connection.port_name)
                while next_sample <= time.monotonic():
                    next_sample += AppConfig.DISPLAY_SAMPLE_SECONDS
            reading = connection.read()
        database.publish_state("stopped", "사용자가 전체 기록기를 종료했습니다.")
    except Exception as exc:
        message = (
            f"센서 {sensor_serial} / {connection.port_name} · "
            f"{type(exc).__name__}: {exc}"
        )
        _write_logger_error(message)
        try:
            database.publish_state("error", message)
        except sqlite3.Error:
            pass
    finally:
        connection.close()
        database.close()


class SensorWorker:
    def __init__(self, connection: SHT41Serial, stop_event: threading.Event):
        self.connection = connection
        self.sensor_serial = connection.first_reading.sensor_serial
        self.thread = threading.Thread(
            target=_run_sensor_connection,
            args=(connection, stop_event),
            name=f"SHT41-{self.sensor_serial}",
        )

    def start(self) -> None:
        self.thread.start()

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def join(self) -> None:
        self.thread.join()


def run_logger() -> None:
    mutex = LoggerMutex.acquire()
    if mutex is None:
        return
    stop_event = threading.Event()
    workers: dict[str, SensorWorker] = {}
    try:
        migrate_legacy_database()
        stop_path = logger_stop_request_path()
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        if stop_path.exists():
            stop_path.unlink()

        while not stop_event.is_set():
            if stop_path.exists():
                stop_event.set()
                break

            for port_name, worker in list(workers.items()):
                if not worker.is_alive():
                    worker.join()
                    del workers[port_name]

            active_serials = {
                worker.sensor_serial
                for worker in workers.values()
                if worker.is_alive()
            }
            for port_name in SHT41Serial.candidate_ports():
                if port_name in workers:
                    continue
                try:
                    connection = SHT41Serial.connect_port(port_name)
                except RuntimeError as exc:
                    _write_logger_error(str(exc))
                    continue
                sensor_serial = connection.first_reading.sensor_serial
                if sensor_serial in active_serials:
                    connection.close()
                    continue
                worker = SensorWorker(connection, stop_event)
                workers[port_name] = worker
                active_serials.add(sensor_serial)
                worker.start()

            time.sleep(AppConfig.SENSOR_SCAN_SECONDS)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _write_logger_error(message)
    finally:
        stop_event.set()
        for worker in workers.values():
            worker.join()
        mutex.close()


def _logger_executable() -> Path:
    if getattr(sys, "frozen", False):
        return APPLICATION_DIRECTORY / "SHT41 Logger.exe"
    return Path(sys.executable)


def _spawn_logger() -> None:
    if getattr(sys, "frozen", False):
        command = [str(_logger_executable())]
    else:
        command = [str(_logger_executable()), str(Path(__file__).resolve()), "--logger"]
    if not Path(command[0]).exists():
        raise RuntimeError("SHT41 Logger.exe가 없습니다. ZIP을 다시 압축 해제하세요.")
    subprocess.Popen(
        command,
        cwd=APPLICATION_DIRECTORY,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=0x00000008 | 0x08000000,
    )


def _configure_windows_autostart() -> bool:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    logger = _logger_executable()
    if not logger.exists():
        raise RuntimeError("SHT41 Logger.exe가 없습니다. ZIP을 다시 압축 해제하세요.")
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(
            key,
            "SHT41 Monitor Logger",
            0,
            winreg.REG_SZ,
            f'"{logger}"',
        )
    return True


def load_active_sensor_summaries(
    now: int | None = None,
) -> list[SensorSummary]:
    current_time = int(time.time()) if now is None else now
    summaries = []
    if not AppConfig.DATA_DIRECTORY.exists():
        return summaries
    for database_path in sorted(
        AppConfig.DATA_DIRECTORY.glob("sensor_*/sht41_history.sqlite3")
    ):
        connection = sqlite3.connect(
            database_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=5.0,
        )
        try:
            status_row = connection.execute(
                """
                SELECT state, updated_utc, sensor_serial, port_name,
                       temp_centi, humidity_centi, message
                FROM runtime_state
                WHERE singleton = 1
                """
            ).fetchone()
            serial_row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'sensor_serial'"
            ).fetchone()
            name_row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'display_name'"
            ).fetchone()
        finally:
            connection.close()
        if status_row is None or serial_row is None:
            continue
        status = RuntimeStatus(
            state=str(status_row[0]),
            updated_utc=int(status_row[1]),
            sensor_serial=None if status_row[2] is None else int(status_row[2]),
            port_name=None if status_row[3] is None else str(status_row[3]),
            temperature_c=(
                None if status_row[4] is None else int(status_row[4]) / 100.0
            ),
            humidity_rh=(
                None if status_row[5] is None else int(status_row[5]) / 100.0
            ),
            message=str(status_row[6]),
        )
        sensor_serial = int(serial_row[0])
        if (
            status.state != "running"
            or status.sensor_serial != sensor_serial
            or status.updated_utc
            < current_time - AppConfig.DISPLAY_SAMPLE_SECONDS * 3
        ):
            continue
        summaries.append(
            SensorSummary(
                sensor_serial=sensor_serial,
                display_name=(
                    "이름 없음" if name_row is None else str(name_row[0])
                ),
                database_path=database_path,
                status=status,
            )
        )
    return sorted(summaries, key=lambda summary: summary.sensor_serial)


def _ensure_logger() -> list[SensorSummary]:
    summaries = load_active_sensor_summaries()
    if summaries:
        return summaries

    logger_started_at = time.time()
    _spawn_logger()
    deadline = time.monotonic() + AppConfig.PROBE_TIMEOUT_SECONDS + 12
    while time.monotonic() < deadline:
        time.sleep(0.2)
        summaries = load_active_sensor_summaries()
        if summaries:
            return summaries
    if (
        AppConfig.LOGGER_ERROR_PATH.exists()
        and AppConfig.LOGGER_ERROR_PATH.stat().st_mtime >= logger_started_at - 1
    ):
        raise RuntimeError(
            AppConfig.LOGGER_ERROR_PATH.read_text(encoding="utf-8").strip()
        )
    raise RuntimeError(
        "연결되어 데이터를 보내는 SHT41 센서를 제한 시간 안에 찾지 못했습니다."
    )


def request_logger_stop() -> None:
    path = logger_stop_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop\n", encoding="ascii")


def _validate_sensor_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("센서 이름을 입력하지 않았습니다.")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError("센서 이름에 제어 문자를 사용할 수 없습니다.")
    if _display_width(cleaned) > AppConfig.MAX_SENSOR_NAME_WIDTH:
        raise ValueError(
            f"센서 이름은 화면 너비 기준 {AppConfig.MAX_SENSOR_NAME_WIDTH}칸 "
            "이하여야 합니다."
        )
    return cleaned


def _read_dashboard_action() -> str | None:
    if os.name != "nt":
        return None
    import msvcrt

    action = None
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            extended_key = msvcrt.getwch()
            if extended_key == "K":
                action = "previous_sensor"
            elif extended_key == "M":
                action = "next_sensor"
        elif key == "\x11":
            return "stop_logger"
        elif key.casefold() == "e":
            action = "export_csv"
        elif key.casefold() == "n":
            action = "rename_sensor"
    return action


def run_dashboard_app() -> None:
    _enable_windows_terminal()
    print("백그라운드 기록기와 SHT41 센서를 확인하는 중입니다...")
    cursor_hidden = False
    database: HistoryDatabase | None = None
    try:
        autostart_enabled = _configure_windows_autostart()
        summaries = _ensure_logger()
        selected_serial = summaries[0].sensor_serial
        last_sensor_name = summaries[0].display_name
        selected_path: Path | None = None
        print("\x1b[2J\x1b[H\x1b[?25l", end="", flush=True)
        cursor_hidden = True
        last_render_signature = None
        notice = None
        notice_until = 0.0
        next_sensor_scan = 0.0
        waiting_rendered = False

        while True:
            monotonic_now = time.monotonic()
            if monotonic_now >= next_sensor_scan:
                summaries = load_active_sensor_summaries()
                if not summaries:
                    if database is not None:
                        database.close()
                        database = None
                    selected_path = None
                    if not waiting_rendered:
                        waiting = render_waiting_screen(
                            last_sensor_name,
                            selected_serial,
                            autostart_enabled,
                        )
                        print(
                            "\x1b[H" + waiting + "\x1b[J",
                            end="",
                            flush=True,
                        )
                        waiting_rendered = True
                    next_sensor_scan = (
                        monotonic_now + AppConfig.SENSOR_SCAN_SECONDS
                    )
                    action = _read_dashboard_action()
                    if action == "stop_logger":
                        request_logger_stop()
                        return
                    time.sleep(0.1)
                    continue
                serials = [summary.sensor_serial for summary in summaries]
                if selected_serial not in serials:
                    selected_serial = serials[0]
                selected_summary = next(
                    summary
                    for summary in summaries
                    if summary.sensor_serial == selected_serial
                )
                if selected_summary.database_path != selected_path:
                    if database is not None:
                        database.close()
                    selected_path = selected_summary.database_path
                    database = HistoryDatabase(selected_path)
                    last_render_signature = None
                waiting_rendered = False
                next_sensor_scan = monotonic_now + AppConfig.SENSOR_SCAN_SECONDS

            if database is None or selected_path is None:
                action = _read_dashboard_action()
                if action == "stop_logger":
                    request_logger_stop()
                    return
                time.sleep(0.1)
                continue
            status = database.load_runtime_status()
            if (
                status is None
                or status.state != "running"
                or status.sensor_serial != selected_serial
                or status.updated_utc
                < int(time.time()) - AppConfig.DISPLAY_SAMPLE_SECONDS * 3
            ):
                database.close()
                database = None
                selected_path = None
                next_sensor_scan = 0.0
                last_render_signature = None
                continue

            if notice is not None and monotonic_now >= notice_until:
                notice = None
                last_render_signature = None
            sensor_name = database.display_name()
            sensor_index = next(
                index
                for index, summary in enumerate(summaries)
                if summary.sensor_serial == selected_serial
            )
            render_signature = (
                selected_serial,
                status.updated_utc,
                sensor_name,
                len(summaries),
                notice,
            )
            if render_signature != last_render_signature:
                last_sensor_name = sensor_name
                oldest = int(time.time()) - max(span for _, span in AppConfig.PANELS)
                points = database.load_dashboard_points(oldest)
                dashboard = render_dashboard(
                    status,
                    points,
                    autostart_enabled,
                    sensor_name=sensor_name,
                    sensor_index=sensor_index,
                    sensor_count=len(summaries),
                    notice=notice,
                )
                print("\x1b[H" + dashboard + "\x1b[J", end="", flush=True)
                last_render_signature = render_signature

            action = _read_dashboard_action()
            if action == "stop_logger":
                request_logger_stop()
                return
            if action in ("previous_sensor", "next_sensor"):
                offset = -1 if action == "previous_sensor" else 1
                selected_serial = summaries[
                    (sensor_index + offset) % len(summaries)
                ].sensor_serial
                next_sensor_scan = 0.0
                continue
            if action == "rename_sensor":
                print("\x1b[2J\x1b[H\x1b[?25h", end="", flush=True)
                entered_name = input(
                    f"센서 {selected_serial}의 새 이름 "
                    f"(현재: {sensor_name}, Enter만 누르면 취소): "
                )
                print("\x1b[2J\x1b[H\x1b[?25l", end="", flush=True)
                if entered_name.strip():
                    try:
                        sensor_name = _validate_sensor_name(entered_name)
                    except ValueError as exc:
                        notice = f"이름 변경 실패 · {exc}"
                    else:
                        database.set_display_name(sensor_name)
                        notice = f"센서 이름 변경 완료 · {sensor_name}"
                    notice_until = time.monotonic() + 8
                last_render_signature = None
            if action == "export_csv":
                csv_path, row_count = export_history_csv(
                    database, sensor_export_directory(selected_serial)
                )
                relative_path = csv_path.relative_to(APPLICATION_DIRECTORY)
                notice = (
                    f"CSV 저장 완료 · {relative_path} · 5분 기록 {row_count:,}행 · "
                    "E 다시 내보내기 · Ctrl+C 화면 닫기"
                )
                notice_until = time.monotonic() + 8
                last_render_signature = None
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if cursor_hidden:
            print("\x1b[0m\x1b[?25h")
        if database is not None:
            database.close()


if __name__ == "__main__":
    if _is_logger_mode():
        run_logger()
    else:
        try:
            run_dashboard_app()
        except Exception as exc:
            print(f"\n오류: {exc}", file=sys.stderr)
            raise SystemExit(1)
