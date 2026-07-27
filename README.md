# SHT41 Terminal Monitor

Adafruit SHT41 Trinkey용 Windows 터미널 모니터 및 백그라운드 기록기입니다. 연결된 센서의 온도와 상대습도를 ASCII/점자 문자 그래프로 표시하고, 센서별 기록을 SQLite에 저장합니다.

## 주요 기능

- 현재 온도·상대습도와 최근 10분, 1시간, 1일, 1주 추세 표시
- 10초 간격 측정, 5분 구간 통계 장기 보관
- 여러 SHT41 Trinkey 동시 수집 및 센서별 데이터 분리
- 센서 이름 지정과 `←/→` 화면 전환
- USB 연결 해제 시 대기 화면 유지, 재연결 시 자동 기록·화면 복구
- Windows 로그인 시 창 없는 Logger 자동 시작
- 선택 센서의 전체 장기 기록을 Excel 호환 CSV로 내보내기

## 지원 환경

- Windows 10/11 64비트
- Adafruit SHT41 Trinkey
- Adafruit 출고 펌웨어

배포 ZIP을 사용하는 경우 Python이나 인터넷 연결은 필요하지 않습니다.

## 설치 및 실행

1. GitHub의 **Releases**에서 최신 Windows x64 ZIP을 받습니다.
2. ZIP을 완전히 압축 해제합니다. ZIP 내부에서 EXE를 직접 실행하지 마세요.
3. SHT41 Trinkey를 USB 포트에 연결합니다.
4. `SHT41 Monitor.exe`를 실행합니다.

`SHT41 Logger.exe`는 화면 없이 측정과 저장을 담당합니다. Monitor가 자동으로 실행·등록하므로 직접 실행할 필요가 없습니다.

기존 버전에서 업데이트할 때는 먼저 Monitor에서 `Ctrl+Q`를 눌러 Logger를 종료한 뒤, 기존 `data` 폴더를 새 프로그램 폴더로 복사하세요.

## 조작

| 키 | 동작 |
|---|---|
| `←` / `→` | 표시할 센서 전환 |
| `N` | 선택 센서 이름 변경 |
| `E` | 선택 센서의 CSV 내보내기 |
| `Ctrl+C` | Monitor 화면만 닫기 |
| `Ctrl+Q` | Monitor와 전체 Logger 종료 |

USB 연결이 끊기면 Monitor는 종료되지 않고 재연결을 기다립니다. 센서가 다시 감지되면 같은 센서 DB에 저장을 재개하고 그래프를 복원합니다. 연결이 끊긴 구간은 실제 데이터 공백으로 남습니다.

## 저장 형식

센서별 데이터는 다음 경로에 저장됩니다.

```text
data/
└─ sensor_<센서 ID>/
   └─ sht41_history.sqlite3
```

- 최근 1주: 10초 측정값 순환 보관
- 장기 기록: 5분 구간별 평균·최저·최고 온습도와 유효 측정 횟수
- CSV: `exports/sensor_<센서 ID>/SHT41_기록_YYYYMMDD_HHMMSS.csv`

기존 단일 DB인 `data/sht41_history.sqlite3`는 실행 시 센서 ID별 폴더로 자동 이전됩니다.

## 소스에서 실행

1. Python 3.12 이상을 설치합니다.
2. `setup.cmd`를 한 번 실행합니다.
3. `launch_monitor.cmd`를 실행합니다.

자동 포트 검색을 제한하려면 `sht41_monitor.py` 상단의 `AppConfig.SERIAL_PORT`에 `"COM4"`처럼 지정할 수 있습니다.

## 테스트와 빌드

```powershell
python -m unittest discover -s tests -v
python build_distribution.py
```

배포 빌드에는 `requirements-dev.txt`의 PyInstaller가 필요합니다. 결과물은 `release/SHT41_Monitor_Windows_x64_v2.zip`에 생성됩니다.

## 라이선스

MIT License
