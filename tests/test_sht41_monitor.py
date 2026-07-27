import csv
import io
import tempfile
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sht41_monitor import (
    AppConfig,
    HistoryDatabase,
    PlotPoint,
    RuntimeStatus,
    SensorSummary,
    SensorReading,
    _configure_windows_autostart,
    _display_width,
    _run_sensor_connection,
    _validate_sensor_name,
    braille_graph,
    export_history_csv,
    logger_stop_request_path,
    load_active_sensor_summaries,
    make_current_header,
    make_panel,
    migrate_legacy_database,
    parse_factory_line,
    render_dashboard,
    render_waiting_screen,
    run_dashboard_app,
    run_logger,
    sensor_database_path,
)


class FactoryParserTests(unittest.TestCase):
    def test_parses_verified_factory_format(self):
        reading = parse_factory_line("1234567890, 20.38, 64.10, 187", timestamp=123)
        self.assertEqual(reading.timestamp, 123)
        self.assertEqual(reading.sensor_serial, 1234567890)
        self.assertEqual(reading.temperature_c, 20.38)
        self.assertEqual(reading.humidity_rh, 64.10)

    def test_rejects_headers_and_out_of_range_values(self):
        invalid = (
            "# Serial number, Temperature in *C, Relative Humidity %, Touch",
            "1234567890, 20.0, 101.0, 187",
            "1234567890, 20.0, 50.0",
        )
        for line in invalid:
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_factory_line(line, timestamp=123)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = HistoryDatabase(Path(self.temp_dir.name) / "history.sqlite3")
        self.db.bind_sensor(1234567890)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_aggregates_mean_min_max_and_count_in_five_minute_bucket(self):
        for timestamp, temp, humidity in (
            (600, 20.00, 60.00),
            (610, 21.00, 62.00),
            (620, 19.50, 61.00),
        ):
            self.db.record(
                SensorReading(timestamp, 1234567890, temp, humidity)
            )

        self.assertEqual(
            self.db.aggregate_for_bucket(600),
            (3, 6050, 1950, 2100, 18300, 6000, 6200),
        )
        point = self.db.load_plot_points(0)[0]
        self.assertAlmostEqual(point.temperature_c, 20.166666, places=5)
        self.assertAlmostEqual(point.humidity_rh, 61.0)
        self.assertEqual(point.sample_count, 3)
        self.assertEqual(len(self.db.load_dashboard_points(0)), 3)

    def test_publishes_current_reading_for_dashboard_process(self):
        reading = SensorReading(620, 1234567890, 20.25, 61.50)
        self.db.publish_running(reading, "COM4")
        status = self.db.load_runtime_status()
        self.assertEqual(
            status,
            RuntimeStatus(
                "running",
                620,
                1234567890,
                "COM4",
                20.25,
                61.50,
                "정상적으로 기록 중입니다.",
            ),
        )

    def test_refuses_to_mix_a_different_sensor(self):
        with self.assertRaises(RuntimeError):
            self.db.bind_sensor(1)

    def test_stores_a_human_name_for_the_sensor(self):
        self.assertEqual(self.db.display_name(), "이름 없음")
        self.db.set_display_name("배양기 내부")
        self.assertEqual(self.db.display_name(), "배양기 내부")

    def test_exports_all_five_minute_history_as_excel_friendly_csv(self):
        self.db.set_display_name("실험실 A")
        for timestamp, temp, humidity in (
            (600, 20.00, 60.00),
            (610, 21.00, 62.00),
            (900, 22.00, 64.00),
        ):
            self.db.record(
                SensorReading(timestamp, 1234567890, temp, humidity)
            )

        export_directory = Path(self.temp_dir.name) / "exports"
        csv_path, row_count = export_history_csv(
            self.db, export_directory, exported_at=1_000
        )

        self.assertEqual(row_count, 2)
        self.assertTrue(csv_path.name.startswith("SHT41_기록_"))
        self.assertTrue(csv_path.name.endswith(".csv"))
        self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with csv_path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]["센서 이름"], "실험실 A")
        self.assertEqual(rows[0]["센서 ID"], "1234567890")
        self.assertEqual(rows[0]["5분 구간 시작 (UTC)"], "1970-01-01T00:10:00Z")
        self.assertEqual(rows[0]["평균 온도 (°C)"], "20.50")
        self.assertEqual(rows[0]["최저 온도 (°C)"], "20.00")
        self.assertEqual(rows[0]["최고 온도 (°C)"], "21.00")
        self.assertEqual(rows[0]["평균 습도 (%RH)"], "61.00")
        self.assertEqual(rows[0]["측정 횟수"], "2")

    def test_migrates_the_legacy_database_to_its_sensor_id_directory(self):
        self.db.close()
        root = Path(self.temp_dir.name) / "data"
        legacy = HistoryDatabase(root / "sht41_history.sqlite3")
        legacy.bind_sensor(1234567890)
        legacy.record(SensorReading(600, 1234567890, 20.0, 60.0))
        legacy.close()

        destination = migrate_legacy_database(root)

        self.assertEqual(destination, sensor_database_path(1234567890, root))
        self.assertFalse((root / "sht41_history.sqlite3").exists())
        migrated = HistoryDatabase(destination)
        self.assertEqual(len(migrated.load_plot_points(0)), 1)
        migrated.close()
        self.db = HistoryDatabase(Path(self.temp_dir.name) / "history.sqlite3")
        self.db.bind_sensor(1234567890)

    def test_lists_all_currently_running_sensor_databases(self):
        self.db.close()
        root = Path(self.temp_dir.name) / "data"
        with patch.object(AppConfig, "DATA_DIRECTORY", root):
            for serial, name, port in (
                (1234567890, "배양기 내부", "COM4"),
                (2345678901, "실험실 A", "COM5"),
            ):
                database = HistoryDatabase(sensor_database_path(serial))
                database.bind_sensor(serial)
                database.set_display_name(name)
                database.publish_running(
                    SensorReading(1_000, serial, 20.0, 60.0), port
                )
                database.close()

            summaries = load_active_sensor_summaries(now=1_000)

        self.assertEqual(
            [summary.sensor_serial for summary in summaries],
            [1234567890, 2345678901],
        )
        self.assertEqual(
            [summary.display_name for summary in summaries],
            ["배양기 내부", "실험실 A"],
        )
        self.db = HistoryDatabase(Path(self.temp_dir.name) / "history.sqlite3")
        self.db.bind_sensor(1234567890)


class GraphTests(unittest.TestCase):
    def test_braille_graph_has_requested_dimensions_and_marks(self):
        points = [
            PlotPoint(0, 20.0, 50.0),
            PlotPoint(10, 21.0, 60.0),
            PlotPoint(20, 20.5, 55.0),
        ]
        graph, _, _ = braille_graph(points, 0, 20, 12, 4, use_color=False)
        self.assertEqual(len(graph), 4)
        self.assertTrue(all(len(line) == 12 for line in graph))
        self.assertTrue(any(character != " " for line in graph for character in line))

    def test_panel_has_plain_language_average_and_labeled_y_axes(self):
        panel = make_panel(
            "최근 10분",
            600,
            [PlotPoint(10, 20.0, 50.0), PlotPoint(20, 21.0, 51.0)],
            20,
            60,
            16,
            use_color=False,
        )
        self.assertEqual(len(panel), 16)
        self.assertTrue(all(_display_width(line) == 60 for line in panel))
        self.assertIn("기간 평균", panel[1])
        self.assertIn("온도", panel[1])
        self.assertIn("습도", panel[1])
        self.assertNotIn("눈금", panel[2])
        self.assertIn("°C", panel[2])
        self.assertIn("%RH", panel[2])

    def test_large_current_value_header_fills_requested_width(self):
        header = make_current_header(21.42, 72.38, 1_000, 140, use_color=False)
        self.assertEqual(len(header), 9)
        self.assertTrue(all(_display_width(line) == 140 for line in header))
        self.assertIn("현재 온도", header[1])
        self.assertIn("현재 상대습도", header[1])

    @patch("sht41_monitor.shutil.get_terminal_size", return_value=(140, 46))
    @patch("sht41_monitor.time.time", return_value=1_000)
    def test_dashboard_fills_terminal_and_explains_controls(self, _, __):
        status = RuntimeStatus(
            "running", 1_000, 1234567890, "COM4", 21.42, 72.38, "running"
        )
        points = [
            PlotPoint(400, 20.0, 70.0),
            PlotPoint(700, 21.0, 71.0),
            PlotPoint(1_000, 21.42, 72.38),
        ]
        dashboard = render_dashboard(
            status,
            points,
            True,
            sensor_name="배양기 내부",
            sensor_index=1,
            sensor_count=2,
            use_color=False,
        )
        lines = dashboard.splitlines()
        self.assertEqual(len(lines), 46)
        self.assertTrue(all(_display_width(line) == 140 for line in lines))
        self.assertIn("배양기 내부", lines[0])
        self.assertIn("ID 1234567890", lines[0])
        self.assertIn("센서 2/2", lines[-1])
        self.assertIn("←/→ 전환", lines[-1])
        self.assertIn("N 이름 변경", lines[-1])
        self.assertIn("E CSV", lines[-1])
        self.assertIn("Ctrl+C 화면 닫기", lines[-1])
        self.assertIn("Ctrl+Q 전체 기록기 종료", lines[-1])

    def test_sensor_name_validation_limits_terminal_width(self):
        self.assertEqual(_validate_sensor_name("  배양기 내부  "), "배양기 내부")
        with self.assertRaises(ValueError):
            _validate_sensor_name("가" * 16)

    @patch("sht41_monitor.shutil.get_terminal_size", return_value=(140, 46))
    def test_waiting_screen_explains_automatic_reconnection(self, _):
        waiting = render_waiting_screen(
            "배양기 내부", 1234567890, True, use_color=False
        )
        lines = waiting.splitlines()
        self.assertEqual(len(lines), 46)
        self.assertTrue(all(_display_width(line) == 140 for line in lines))
        self.assertIn("SHT41 센서 재연결 대기 중", waiting)
        self.assertIn("배양기 내부 · ID 1234567890", waiting)
        self.assertIn("자동으로 이어갑니다", waiting)
        self.assertIn("Ctrl+Q 전체 기록기 종료", lines[-1])

    def test_monitor_waits_and_restores_dashboard_after_reconnection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "sht41_history.sqlite3"
            database = HistoryDatabase(database_path)
            database.bind_sensor(1234567890)
            database.set_display_name("배양기 내부")
            now = int(time.time())
            database.record(
                SensorReading(now, 1234567890, 21.42, 72.38)
            )
            database.publish_running(
                SensorReading(now, 1234567890, 21.42, 72.38),
                "COM4",
            )
            status = database.load_runtime_status()
            database.close()
            summary = SensorSummary(
                1234567890, "배양기 내부", database_path, status
            )
            scans = iter(([], [summary]))

            def scan_sensors():
                return next(scans, [summary])

            output = io.StringIO()
            with (
                patch("sht41_monitor._enable_windows_terminal"),
                patch(
                    "sht41_monitor._configure_windows_autostart",
                    return_value=True,
                ),
                patch("sht41_monitor._ensure_logger", return_value=[summary]),
                patch(
                    "sht41_monitor.load_active_sensor_summaries",
                    side_effect=scan_sensors,
                ),
                patch("sht41_monitor._read_dashboard_action", return_value=None),
                patch.object(AppConfig, "SENSOR_SCAN_SECONDS", 0),
                patch(
                    "sht41_monitor.shutil.get_terminal_size",
                    return_value=(140, 46),
                ),
                patch(
                    "sht41_monitor.time.sleep",
                    side_effect=[None, KeyboardInterrupt],
                ),
                patch("sys.stdout", output),
            ):
                run_dashboard_app()

            rendered = output.getvalue()
            self.assertIn("SHT41 센서 재연결 대기 중", rendered)
            self.assertIn("배양기 내부 · ID 1234567890 · COM4", rendered)


class BackgroundLoggerTests(unittest.TestCase):
    def test_autostart_registers_windowless_logger_for_current_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = Path(temp_dir) / "SHT41 Logger.exe"
            logger.touch()
            key = MagicMock()
            key.__enter__.return_value = "registry-key"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch("sht41_monitor._logger_executable", return_value=logger),
                patch("winreg.OpenKey", return_value=key),
                patch("winreg.SetValueEx") as set_value,
            ):
                self.assertTrue(_configure_windows_autostart())
            set_value.assert_called_once()
            arguments = set_value.call_args.args
            self.assertEqual(arguments[1], "SHT41 Monitor Logger")
            self.assertEqual(arguments[4], f'"{logger}"')

    def test_logger_starts_a_worker_for_every_connected_port(self):
        class FakeConnection:
            def __init__(self, serial, port):
                self.port_name = port
                self.first_reading = SensorReading(600, serial, 20.0, 60.0)

            def close(self):
                pass

        class FakeWorker:
            def __init__(self, connection, _stop_event):
                self.sensor_serial = connection.first_reading.sensor_serial
                self.started = False
                self.joined = False

            def start(self):
                self.started = True

            def is_alive(self):
                return True

            def join(self):
                self.joined = True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            fake_mutex = MagicMock()
            workers = []

            def make_worker(connection, stop_event):
                worker = FakeWorker(connection, stop_event)
                workers.append(worker)
                return worker

            def request_stop(_seconds):
                path = logger_stop_request_path(root)
                path.write_text("stop\n", encoding="ascii")

            with (
                patch.object(AppConfig, "DATA_DIRECTORY", root),
                patch("sht41_monitor.LoggerMutex.acquire", return_value=fake_mutex),
                patch(
                    "sht41_monitor.SHT41Serial.candidate_ports",
                    return_value=["COM4", "COM5"],
                ),
                patch(
                    "sht41_monitor.SHT41Serial.connect_port",
                    side_effect=[
                        FakeConnection(1234567890, "COM4"),
                        FakeConnection(2345678901, "COM5"),
                    ],
                ),
                patch("sht41_monitor.SensorWorker", side_effect=make_worker),
                patch("sht41_monitor.time.sleep", side_effect=request_stop),
            ):
                run_logger()

            self.assertEqual(len(workers), 2)
            self.assertTrue(all(worker.started for worker in workers))
            self.assertTrue(all(worker.joined for worker in workers))
            fake_mutex.close.assert_called_once()

    def test_each_sensor_connection_records_to_its_own_id_directory(self):
        class FakeConnection:
            def __init__(self, serial, port):
                self.port_name = port
                self.first_reading = SensorReading(600, serial, 20.0, 60.0)
                self.readings = [
                    SensorReading(610, serial, 20.5, 61.0),
                    SensorReading(620, serial, 21.0, 62.0),
                ]

            def read(self):
                return self.readings.pop(0)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            for serial, port in (
                (1234567890, "COM4"),
                (2345678901, "COM5"),
            ):
                stop_event = MagicMock(spec=threading.Event)
                stop_event.is_set.side_effect = [False, False, True]
                with (
                    patch.object(AppConfig, "DATA_DIRECTORY", root),
                    patch(
                        "sht41_monitor.time.monotonic",
                        side_effect=[0, 0, 0, 0, 11, 11, 11],
                    ),
                ):
                    _run_sensor_connection(
                        FakeConnection(serial, port), stop_event
                    )

                database = HistoryDatabase(sensor_database_path(serial, root))
                self.assertEqual(
                    database.connection.execute(
                        "SELECT SUM(sample_count) FROM readings_5m"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(database.load_runtime_status().state, "stopped")
                database.close()


if __name__ == "__main__":
    unittest.main()
