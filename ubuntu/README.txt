SHT41 Terminal Monitor - Ubuntu Server 26.04 LTS
================================================

Purpose
-------
Connect an Adafruit SHT41 Trinkey directly to a USB port of the Ubuntu
server. A systemd logger records it continuously, and the monitor shows live
graphs in an SSH terminal.

CPU architecture
----------------
This package installs Python source, not a binary, so the same ZIP works on
every CPU that Ubuntu 26.04 supports.

Install
-------
1. Copy the ZIP to the Ubuntu server.
2. Log in over SSH as a regular user. Do not log in as root.
3. In the folder that contains the ZIP, run:

   sudo apt-get install -y unzip
   unzip SHT41_Monitor_Ubuntu_26.04_v2.1.0.zip
   cd SHT41_Monitor_Ubuntu
   sudo ./install.sh

The installer needs internet access because it installs python3 and
python3-serial from the official Ubuntu archive. The user who ran it owns the
history files and sensor names.

Live monitor
------------
After logging in over SSH, run:

   sht41-monitor

The terminal must be at least 120 columns x 42 lines.

  Left/Right arrows   switch the displayed sensor
  N                   rename the selected sensor
  E                   export the selected sensor's full history to CSV
  Ctrl+C              close the monitor only

The systemd logger keeps running when the monitor or SSH session ends, and
it starts again automatically after a reboot. If the USB connection drops it
keeps scanning, and when the sensor is plugged back in it resumes logging
into the same per-sensor database.

Logger status
-------------
  systemctl status sht41-logger.service
  sudo journalctl -u sht41-logger.service -f

Data location
-------------
  SQLite  /var/lib/sht41-terminal-monitor/data/sensor_<ID>/sht41_history.sqlite3
  CSV     /var/lib/sht41-terminal-monitor/exports/sensor_<ID>/

SQLite stores Unix UTC timestamps. CSV files contain both UTC and the
server's local time, so check the server time zone first if you want CSV
times in a particular zone:

  timedatectl
  sudo timedatectl set-timezone Asia/Seoul    # example

Update
------
Unzip the new ZIP and run sudo ./install.sh again. It stops the systemd
service, replaces the program and starts the service again. Sensor databases
and CSV files under /var/lib are never deleted or overwritten.

Troubleshooting
---------------
If no sensor shows up, check:

  ls -l /dev/ttyACM* /dev/ttyUSB*
  systemctl status sht41-logger.service
  sudo journalctl -u sht41-logger.service -n 100 --no-pager

The Trinkey must run the Adafruit factory firmware, which sends the sensor
ID, temperature, humidity and touch value as CSV over USB serial.
