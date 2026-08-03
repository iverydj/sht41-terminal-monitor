SHT41 Terminal Monitor - Ubuntu Server 26.04 LTS
================================================

용도
----
Adafruit SHT41 Trinkey를 Ubuntu 서버 USB 포트에 직접 연결하고,
systemd 로거로 계속 기록하면서 SSH 터미널에서 실시간 그래프를 봅니다.

CPU 종류
---------
이 배포본은 실행 파일이 아니라 Python 소스로 설치됩니다. Ubuntu 26.04가
지원하는 CPU라면 동일한 ZIP을 사용합니다.

설치
----
1. ZIP을 Ubuntu 서버로 복사합니다.
2. 일반 사용자 계정으로 SSH 접속합니다. root로 직접 로그인하지 마세요.
3. ZIP이 있는 폴더에서 다음 순서로 설치합니다.

   sudo apt-get install -y unzip
   unzip SHT41_Monitor_Ubuntu_26.04_v2.1.0.zip
   cd SHT41_Monitor_Ubuntu
   sudo ./install.sh

설치 과정에서 Ubuntu 공식 저장소의 python3와 python3-serial 패키지를
설치하므로 인터넷 연결이 필요합니다. 설치한 일반 사용자가 기록 파일과
센서 이름을 관리합니다.

실시간 모니터
-------------
SSH 접속 후 다음 명령을 실행합니다.

   sht41-monitor

터미널 크기는 최소 120열 x 42행이 필요합니다.

  왼쪽/오른쪽 방향키  표시 센서 전환
  N                    센서 이름 변경
  E                    선택 센서 전체 기록을 CSV로 내보내기
  Ctrl+C               모니터 화면만 종료

SSH 연결이나 모니터가 종료돼도 systemd 로거는 계속 실행됩니다. Ubuntu를
재부팅해도 자동 시작합니다. USB 연결이 끊기면 계속 재검색하고, 다시 꽂으면
같은 센서 ID의 DB에 기록을 자동으로 재개합니다.

기록 상태 확인
--------------
  systemctl status sht41-logger.service
  sudo journalctl -u sht41-logger.service -f

데이터 위치
-----------
  SQLite  /var/lib/sht41-terminal-monitor/data/sensor_<센서ID>/sht41_history.sqlite3
  CSV     /var/lib/sht41-terminal-monitor/exports/sensor_<센서ID>/

SQLite에는 Unix UTC 타임스탬프가 저장됩니다. CSV에는 UTC와 Ubuntu 서버의
현지 시간이 함께 기록됩니다. 한국 시간으로 표시하려면 서버 시간대를 먼저
확인하세요.

  timedatectl
  sudo timedatectl set-timezone Asia/Seoul

업데이트
--------
새 ZIP을 풀고 sudo ./install.sh를 다시 실행합니다. systemd 서비스를 잠시
중지하고 프로그램 환경을 교체한 뒤 다시 시작합니다. /var/lib 아래의 센서
DB와 CSV는 삭제하거나 덮어쓰지 않습니다.

문제 해결
---------
센서가 보이지 않으면 다음 항목을 확인합니다.

  ls -l /dev/ttyACM* /dev/ttyUSB*
  systemctl status sht41-logger.service
  sudo journalctl -u sht41-logger.service -n 100 --no-pager

Adafruit 출고 펌웨어가 센서 ID, 온도, 습도, 터치 값을 USB 직렬 CSV로
보내는 상태여야 합니다.
