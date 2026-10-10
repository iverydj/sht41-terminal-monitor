"""Regenerate docs/screenshot.png from the monitor's own drawing code.

Simulated factory-firmware lines for two sensors go through the real parser and
SQLite history, are loaded back the way the monitor loads them, and are drawn
with render_dashboard(). The ANSI frame is then rasterised as a dark terminal
window. Like most terminal emulators, box-drawing, block and braille characters
are drawn as geometry so they join seamlessly; other text uses the font.
Requires Pillow and the DejaVu Sans Mono font:

    python tools/make_screenshot.py
"""

from __future__ import annotations

import math
import os
import random
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sht41_monitor as monitor  # noqa: E402


class AppConfig:
    OUTPUT_PATH = ROOT / "docs" / "screenshot.png"

    END_TIME = datetime(2026, 10, 9, 15, 42, 30)  # local time of the newest reading
    RANDOM_SEED = 46
    SENSORS = (
        # placeholder serial, name, port, mean °C, mean %RH
        (1234567890, "Lab bench", "COM4", 24.0, 45.0),
        (2345678901, "Storage room", "COM5", 23.4, 48.0),
    )
    SELECTED_SENSOR = 0
    AUTOSTART_ENABLED = True
    ALLOW_LOGGER_STOP = True  # Windows footer, which offers Ctrl+Q
    TERMINAL_COLUMNS = 140
    TERMINAL_LINES = 46
    WINDOW_TITLE = "SHT41 Temperature & Humidity Monitor"

    FONT_FILE = "DejaVuSansMono.ttf"  # name searched in system font folders, or a path
    FONT_SIZE = 16
    TITLE_FONT_SIZE = 13
    CELL_HEIGHT = 20  # four braille dot rows of 5 px
    TEXT_Y_OFFSET = 1
    BRAILLE_DOT_DIAMETER = 2.8
    PADDING = 10
    TITLE_BAR_HEIGHT = 34
    PALETTE_COLORS = 64

    # Windows Terminal "Campbell" scheme
    BACKGROUND = (12, 12, 12)
    FOREGROUND = (204, 204, 204)
    SGR_COLORS = {
        "91": (231, 72, 86),  # bright red: temperature
        "95": (180, 0, 158),  # bright magenta: overlapping lines
        "96": (97, 214, 214),  # bright cyan: humidity
    }
    DIM_FACTOR = 0.55
    TITLE_BAR = (32, 32, 32)
    TITLE_TEXT = (230, 230, 230)
    WINDOW_BORDER = (60, 60, 60)


SAMPLE_SECONDS = monitor.AppConfig.DISPLAY_SAMPLE_SECONDS
HISTORY_SECONDS = max(span for _, span in monitor.AppConfig.PANELS)
SGR_PATTERN = re.compile(r"(\x1b\[[0-9;]*m)")
# Arms of the light box-drawing characters the monitor uses: left/right/up/down.
BOX_ARMS = {
    "─": "lr",
    "│": "ud",
    "┌": "rd",
    "┐": "ld",
    "└": "ru",
    "┘": "lu",
    "├": "udr",
    "┤": "udl",
}


def simulated_factory_lines(
    serial: int, mean_temperature: float, mean_humidity: float, rng: random.Random
):
    """One week of 10-second lines: diurnal and weather cycles, thermostat
    cycling, slow random wander and sensor noise."""
    end = int(AppConfig.END_TIME.timestamp())
    start = end - HISTORY_SECONDS + SAMPLE_SECONDS
    temperature_phase = rng.uniform(0.0, 2.0 * math.pi)
    humidity_phase = rng.uniform(0.0, 2.0 * math.pi)
    decay = math.exp(-SAMPLE_SECONDS / (25 * 60))
    innovation = math.sqrt(1.0 - decay * decay)
    temperature_wander = 0.0
    humidity_wander = 0.0
    for timestamp in range(start, end + 1, SAMPLE_SECONDS):
        local = datetime.fromtimestamp(timestamp)
        hour = local.hour + local.minute / 60 + local.second / 3600
        day = (timestamp - start) / 86_400
        diurnal = math.sin(2.0 * math.pi * (hour - 9.0) / 24.0)  # warmest at 15:00
        temperature_wander = (
            temperature_wander * decay + rng.gauss(0.0, 0.10 * innovation)
        )
        humidity_wander = humidity_wander * decay + rng.gauss(0.0, 0.60 * innovation)
        temperature = (
            mean_temperature
            + 1.1 * diurnal
            + 0.4 * math.sin(2.0 * math.pi * day / 4.5 + temperature_phase)
            + 0.12 * math.sin(2.0 * math.pi * timestamp / (22 * 60))
            + temperature_wander
            + rng.gauss(0.0, 0.02)
        )
        humidity = (
            mean_humidity
            - 2.5 * (temperature - mean_temperature)
            + 2.5 * math.sin(2.0 * math.pi * day / 3.7 + humidity_phase)
            + humidity_wander
            + rng.gauss(0.0, 0.08)
        )
        touch = rng.randint(170, 200)
        yield timestamp, f"{serial}, {temperature:.2f}, {humidity:.2f}, {touch}"


def seed_history() -> None:
    for index, (serial, name, port, mean_temperature, mean_humidity) in enumerate(
        AppConfig.SENSORS
    ):
        database = monitor.HistoryDatabase(monitor.sensor_database_path(serial))
        # Throwaway database: skip fsync and journal files so a week seeds quickly.
        database.connection.execute("PRAGMA synchronous=OFF")
        database.connection.execute("PRAGMA journal_mode=MEMORY")
        database.bind_sensor(serial)
        database.set_display_name(name)
        rng = random.Random(AppConfig.RANDOM_SEED + index)
        reading = None
        for timestamp, line in simulated_factory_lines(
            serial, mean_temperature, mean_humidity, rng
        ):
            reading = monitor.parse_factory_line(line, timestamp=timestamp)
            database.record(reading)
        database.publish_running(reading, port)
        database.close()


def render_frame() -> str:
    """Load the history like run_dashboard_app() does and draw one frame."""
    end = int(AppConfig.END_TIME.timestamp())
    summaries = monitor.load_active_sensor_summaries(now=end)
    if len(summaries) != len(AppConfig.SENSORS):
        raise RuntimeError(f"expected {len(AppConfig.SENSORS)} active sensors")
    database = monitor.HistoryDatabase(
        summaries[AppConfig.SELECTED_SENSOR].database_path
    )
    try:
        status = database.load_runtime_status()
        points = database.load_dashboard_points(end - HISTORY_SECONDS)
        os.environ["COLUMNS"] = str(AppConfig.TERMINAL_COLUMNS)
        os.environ["LINES"] = str(AppConfig.TERMINAL_LINES)
        with patch.object(monitor.time, "time", return_value=end):
            return monitor.render_dashboard(
                status,
                points,
                AppConfig.AUTOSTART_ENABLED,
                sensor_name=database.display_name(),
                sensor_index=AppConfig.SELECTED_SENSOR,
                sensor_count=len(summaries),
                notice=None,
                allow_logger_stop=AppConfig.ALLOW_LOGGER_STOP,
            )
    finally:
        database.close()


def styled_characters(line: str):
    """Yield (character, colour) pairs, applying the SGR codes the monitor emits."""
    color = None
    dim = False
    for chunk in SGR_PATTERN.split(line):
        if SGR_PATTERN.fullmatch(chunk):
            for parameter in chunk[2:-1].split(";"):
                if parameter in ("", "0"):
                    color, dim = None, False
                elif parameter == "2":
                    dim = True
                elif parameter in AppConfig.SGR_COLORS:
                    color = parameter
                else:
                    raise ValueError(f"unexpected SGR parameter {parameter!r}")
            continue
        if "\x1b" in chunk:
            raise ValueError(f"unexpected escape sequence in {chunk!r}")
        rgb = AppConfig.FOREGROUND if color is None else AppConfig.SGR_COLORS[color]
        if dim:
            rgb = tuple(
                round(b + (c - b) * AppConfig.DIM_FACTOR)
                for c, b in zip(rgb, AppConfig.BACKGROUND, strict=True)
            )
        for character in chunk:
            yield character, rgb


def braille_dot_mask(diameter: float) -> Image.Image:
    size = math.ceil(diameter)
    scale = 16
    large = Image.new("L", (size * scale, size * scale), 0)
    offset = (size - diameter) / 2 * scale
    ImageDraw.Draw(large).ellipse(
        (offset, offset, offset + diameter * scale - 1, offset + diameter * scale - 1),
        fill=255,
    )
    return large.resize((size, size), Image.Resampling.LANCZOS)


def draw_box_character(
    draw: ImageDraw.ImageDraw,
    character: str,
    left: int,
    top: int,
    width: int,
    height: int,
    rgb: tuple[int, int, int],
) -> None:
    if character == "█":
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill=rgb)
        return
    arms = BOX_ARMS.get(character)
    if arms is None:
        raise ValueError(f"no drawing rule for U+{ord(character):04X}")
    center_x = left + width // 2
    center_y = top + height // 2
    ends = {
        "l": (left, center_y),
        "r": (left + width - 1, center_y),
        "u": (center_x, top),
        "d": (center_x, top + height - 1),
    }
    for arm in arms:
        draw.line(((center_x, center_y), ends[arm]), fill=rgb, width=1)


def draw_window_controls(draw: ImageDraw.ImageDraw, width: int) -> None:
    middle = AppConfig.TITLE_BAR_HEIGHT // 2
    color = AppConfig.TITLE_TEXT
    close_x = width - 23
    draw.line((close_x - 5, middle - 5, close_x + 5, middle + 5), fill=color)
    draw.line((close_x - 5, middle + 5, close_x + 5, middle - 5), fill=color)
    maximize_x = close_x - 46
    draw.rectangle(
        (maximize_x - 5, middle - 5, maximize_x + 5, middle + 5), outline=color
    )
    minimize_x = maximize_x - 46
    draw.line((minimize_x - 5, middle, minimize_x + 5, middle), fill=color)


def rasterise(frame: str) -> Image.Image:
    font = ImageFont.truetype(AppConfig.FONT_FILE, AppConfig.FONT_SIZE)
    title_font = ImageFont.truetype(AppConfig.FONT_FILE, AppConfig.TITLE_FONT_SIZE)
    cell_width = font.getlength("M")
    if cell_width != int(cell_width):
        raise RuntimeError(f"font advance {cell_width} px is not a whole pixel")
    cell_width = int(cell_width)
    cell_height = AppConfig.CELL_HEIGHT

    lines = frame.split("\n")
    if len(lines) != AppConfig.TERMINAL_LINES:
        raise RuntimeError(f"frame has {len(lines)} lines")
    width = AppConfig.TERMINAL_COLUMNS * cell_width + 2 * AppConfig.PADDING
    height = (
        AppConfig.TITLE_BAR_HEIGHT
        + AppConfig.TERMINAL_LINES * cell_height
        + 2 * AppConfig.PADDING
    )
    image = Image.new("RGB", (width, height), AppConfig.BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (0, 0, width - 1, AppConfig.TITLE_BAR_HEIGHT - 1), fill=AppConfig.TITLE_BAR
    )
    draw.text(
        (14, AppConfig.TITLE_BAR_HEIGHT // 2),
        AppConfig.WINDOW_TITLE,
        font=title_font,
        fill=AppConfig.TITLE_TEXT,
        anchor="lm",
    )
    draw_window_controls(draw, width)

    dot = braille_dot_mask(AppConfig.BRAILLE_DOT_DIAMETER)
    dot_pitch_x = cell_width / 2
    dot_pitch_y = cell_height / 4
    for row, line in enumerate(lines):
        top = AppConfig.TITLE_BAR_HEIGHT + AppConfig.PADDING + row * cell_height
        column = 0
        for character, rgb in styled_characters(line):
            character_width = monitor._display_width(character)
            if character_width == 0:
                continue
            left = AppConfig.PADDING + column * cell_width
            code_point = ord(character)
            if 0x2800 <= code_point <= 0x28FF:
                bits = code_point - 0x2800
                for dot_row, row_bits in enumerate(monitor.BRAILLE_BITS):
                    for dot_column, bit in enumerate(row_bits):
                        if bits & bit:
                            x = left + (dot_column + 0.5) * dot_pitch_x - dot.width / 2
                            y = top + (dot_row + 0.5) * dot_pitch_y - dot.height / 2
                            image.paste(rgb, (round(x), round(y)), dot)
            elif 0x2500 <= code_point <= 0x259F:
                draw_box_character(
                    draw, character, left, top, cell_width, cell_height, rgb
                )
            elif character != " ":
                draw.text(
                    (left, top + AppConfig.TEXT_Y_OFFSET),
                    character,
                    font=font,
                    fill=rgb,
                )
            column += character_width
        if column != AppConfig.TERMINAL_COLUMNS:
            raise RuntimeError(f"line {row} is {column} columns wide")
    draw.rectangle((0, 0, width - 1, height - 1), outline=AppConfig.WINDOW_BORDER)
    return image


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        monitor.AppConfig.STATE_DIRECTORY = Path(temporary)
        monitor.AppConfig.DATA_DIRECTORY = Path(temporary) / "data"
        seed_history()
        frame = render_frame()
    image = rasterise(frame).quantize(
        colors=AppConfig.PALETTE_COLORS, dither=Image.Dither.NONE
    )
    AppConfig.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(AppConfig.OUTPUT_PATH, optimize=True)
    print(
        f"{AppConfig.OUTPUT_PATH} {image.width}x{image.height} "
        f"{AppConfig.OUTPUT_PATH.stat().st_size // 1024} KiB"
    )


if __name__ == "__main__":
    main()
