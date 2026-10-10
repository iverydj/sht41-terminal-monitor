SHT41 Terminal Monitor v2 for Windows
=====================================

Requirements: Windows 10 or 11 (64-bit) and an Adafruit SHT41 Trinkey with
the Adafruit factory firmware. Python and an internet connection are not
needed.


1. First run
------------

1) Right-click the ZIP and choose "Extract All". Do not run the EXE from
   inside the ZIP.
2) Put the extracted "SHT41 Monitor" folder somewhere writable, such as the
   Desktop or Documents. Avoid Program Files and other admin-only folders.
3) Plug the SHT41 Trinkey into any USB port; several can be connected at
   once. On a PC that has not seen the sensor before, give Windows a moment
   to set up the device.
4) Double-click "SHT41 Monitor.exe". It finds the Adafruit USB devices and
   their COM ports automatically.

"SHT41 Logger.exe" is the windowless logger that takes and stores the
readings. You never need to start it yourself, but do not delete or rename it.

Updating from an earlier version:
1) Close the old monitor and stop its logger (Ctrl+Q in the monitor).
2) Copy the old "data" folder into the new "SHT41 Monitor" folder.
3) Run the new "SHT41 Monitor.exe". Your history continues where it left off.


2. The screen
-------------

The top line shows the selected sensor's name, ID and COM port. The large
numbers are the current temperature and relative humidity, refreshed every
10 seconds. A newly found sensor is called "Unnamed".

  Left/Right arrows  switch between connected sensors
  N                  give the selected sensor a readable name
  E                  export the selected sensor's history to CSV
  Ctrl+C             close the monitor (logging continues)
  Ctrl+Q             close the monitor and stop the logger for all sensors

Names are stored with the sensor ID, so they survive a different USB port or
reconnecting. The logger records every connected sensor at the same time; the
monitor shows one sensor at a time.

The four panels show the last 10 minutes, 1 hour, 24 hours and 7 days, each
with that period's mean temperature, mean humidity and number of samples.

  - left axis: °C; right axis: %RH (each series has its own scale)
  - bottom axis: from the start of the period to now
  - red: temperature; cyan: relative humidity; magenta: both lines in the
    same character cell

If the USB connection drops, the monitor stays open and shows "Waiting for
the SHT41 sensor to reconnect". When the sensor is plugged back in, logging
resumes in the same per-sensor file and the dashboard comes back. Time while
disconnected is left as a gap, never filled in. If only the selected sensor
disconnects while others stay connected, the monitor switches to one of them.


3. CSV export
-------------

Press E to write the selected sensor's full long-term history to a new file
(the logger keeps running):

    exports\sensor_<ID>\SHT41_history_YYYYMMDD_HHMMSS.csv

Each row is one 5-minute interval: sensor name and ID, interval start in
local time and UTC, mean/min/max temperature (°C), mean/min/max relative
humidity (%RH), and the number of valid samples. The file is UTF-8 with a
byte-order mark, so Excel displays non-English sensor names correctly. Every
export creates a new file; existing files are never overwritten.


4. Logging in the background
----------------------------

Closing the monitor with Ctrl+C or the window's X button does not stop the
logger. Running "SHT41 Monitor.exe" again reconnects to it.

Ctrl+Q stops the logger for all sensors. It starts again the next time you
open the monitor or sign in to Windows.

On first run the monitor registers "SHT41 Monitor Logger" to start at Windows
sign-in for the current user; no administrator rights are needed. The logger
records every connected sensor and picks up newly connected ones
automatically. If you move the program folder, run "SHT41 Monitor.exe" once
from the new location to update the autostart entry.


5. Data files
-------------

On first run each sensor gets its own file next to the EXE:

    data\sensor_1234567890\sht41_history.sqlite3
    data\sensor_2345678901\sht41_history.sqlite3

Each file holds only that sensor's data: 10-second readings for the last
7 days (for the graphs) and, for the long term, one row per 5 minutes with
mean, minimum and maximum temperature and humidity and the number of valid
samples. Values are stored as integers in hundredths, so the file is smaller
than a CSV. Restarting the program reloads the history and restores the
graphs.

A v1 "data\sht41_history.sqlite3" is moved automatically into the matching
"data\sensor_<ID>" folder.

To back up the history or move it to another PC, stop the logger with Ctrl+Q
and copy the whole "data" folder. Readings from different sensors never mix,
because each sensor ID has its own file.


6. Troubleshooting
------------------

[Sensor not found]

- Unplug the Trinkey and plug it back in.
- Close older SHT41 Monitor windows and their background processes.
- Close programs that may hold the COM port, such as Arduino Serial Monitor,
  Mu Editor or PuTTY.
- Check that a USB serial device appears under "Ports (COM & LPT)" in
  Device Manager.
- If the sensor firmware was changed, restore the Adafruit factory firmware.

[Cannot create the data file]

- Move the program folder to the Desktop or Documents.
- Do not run it from a read-only USB drive or from Program Files.

[Windows protection screen]

The EXE has no commercial code signature, so Microsoft Defender SmartScreen
may report an "Unknown publisher". Choose "More info" and run it only if the
ZIP came from a source you trust.

[Started twice]

Avoid opening several monitor windows. The logger blocks duplicate instances
itself, so only one process ever records the sensors.
