# Sherpa iCal Automation

Turn a Sherpa Online bookings snapshot into an iCalendar file for Apple Calendar,
Google Calendar, Outlook, or another app that accepts `.ics`.

## Export a file

Run from this project directory with Python 3.14 or later. No Python packages
are required.

```sh
PYTHONPATH=src python3 -m sherpa_ical_automation export
```

This reads `PII.json` and writes `sherpa.ics`. Use `--source` and `--output` to
choose different paths. If the project is installed, you can also run
`sherpa-ical-automation export`.

| Calendar | Import the file |
| --- | --- |
| Apple Calendar on Mac | Choose **File → Import**, then choose `sherpa.ics`. [Apple support](https://support.apple.com/guide/calendar/import-or-export-calendars-icl1023/mac) |
| Google Calendar on the web | Choose **Settings → Import & export**, then select `sherpa.ics`. [Google support](https://support.google.com/calendar/answer/37118?hl=en-GB) |
| Outlook on the web | Choose **Add calendar → Upload from file**, then select `sherpa.ics`. [Microsoft support](https://support.microsoft.com/en-gb/outlook/import-or-subscribe-to-a-calendar-in-outlook-com-or-outlook-on-the-web) |

Import into a dedicated Sherpa calendar so you can replace the snapshot later.
Imported calendars display the lessons as they were when the file was generated.

## Run the free twice-daily Mac automation

```sh
uv sync --extra sync
.venv/bin/sherpa-ical-automation install-mac
.venv/bin/sherpa-ical-automation connect
```

Install requires Python 3.14, uv and Google Chrome. During `connect`, sign in
to Sherpa in the dedicated Chrome window, open **Your Bookings**, then press
Return in Terminal. This first scan also checks which response on that page
carries your bookings. The browser keeps its signed-in profile locally in
`.sherpa/`.

The feed starts empty on a fresh checkout and fills after the first successful
scan. To start from an existing Sherpa JSON snapshot, pass `--seed PATH` to
`install-mac`.

The installer starts the feed in the background and schedules scans at
**08:00 and 20:00 in the Mac's time zone**. The jobs run while you are logged
into macOS. A scheduled run missed during sleep can run when the Mac wakes.
The feed keeps showing the most recent good snapshot if Sherpa is unreachable
or the sign-in expires. [macOS scheduling](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)

The installed schedule is in
`~/Library/LaunchAgents/local.sherpa-ical-automation.scan.plist`, under
`StartCalendarInterval`. To change it, edit the hours in
`src/sherpa_ical_automation/macos.py` and rerun `install-mac`.

In Apple Calendar, choose **File → New Calendar Subscription** and paste
`http://127.0.0.1:8765/sherpa.ics`. Apple Calendar may prompt about HTTP when
adding this loopback URL. Set **Location** to **On My Mac** and **Auto-refresh**
to **Every hour**. If you already subscribed to this URL, refresh the existing
calendar. The URL is available on this Mac. [Apple support](https://support.apple.com/guide/calendar/subscribe-to-calendars-icl1022/mac)

### Check or run a scan

```sh
.venv/bin/sherpa-ical-automation sync
```

Scheduled scan results go to `.sherpa/scan.log`. Run `connect` again if Sherpa
asks you to sign in. To export the most recent scanned results as a portable
file, run `.venv/bin/sherpa-ical-automation export --source .sherpa/bookings.json`.

### Reuse a signed-in profile from Chrome's profile picker

Chrome and Playwright require a separate data directory for the direct browser
scanner. The Sherpa Calendar Sync extension runs in the regular Chrome profile
you choose and uses its existing Sherpa sign-in. It opens Your Bookings in a
background tab and sends the booking data to the feed on this Mac.
[Chrome's profile restriction](https://developer.chrome.com/blog/remote-debugging-port)

Run these commands once from the project directory:

```sh
.venv/bin/sherpa-ical-automation prepare-chrome
.venv/bin/sherpa-ical-automation install-mac
```

Switch to the Chrome profile already signed into Sherpa. Open
`chrome://extensions`, turn on **Developer mode**, and use **Load unpacked** to
select the `.sherpa/chrome-extension` folder the command printed. In the folder
picker, press **Command–Shift–G** to paste its absolute path. The extension
checks for the first scan automatically after installation. If **Your
Bookings** uses a different address, pass `--bookings-url URL` to
`prepare-chrome`. The extension remembers the address after a successful scan.

The existing macOS job queues a scan at **08:00 and 20:00**. Chrome picks it up
within a minute while the signed-in profile is open, or the next time you open
that profile. The `sync` command queues a scan on demand and the extension button
starts one right away. The last result is in `.sherpa/chrome-result.json`. An
expired Sherpa session leaves the last successful calendar in place.

### Use a saved snapshot

To serve the original snapshot manually, run
`PYTHONPATH=src python3 -m sherpa_ical_automation serve` with the background
feed stopped, or choose another port with `--port`.

Replace `PII.json` with a fresh saved snapshot when your bookings change. If
you capture your snapshot in a signed-in browser, look in **Network →
Fetch/XHR** while reloading **Your Bookings** for the booking JSON containing
`status` and `meetingDateTime`. Save the JSON response as `PII.json`.

The manual feed reads that file on each refresh. For a file import, regenerate
`sherpa.ics` and replace the contents of the dedicated calendar. Sherpa's
documented Google and Outlook integration uses external events to block
availability in Sherpa.
[Sherpa calendar integration](https://intercom.help/sherpa-online/en/articles/8442861-how-do-i-sync-my-google-or-outlook-calendar-on-sherpa)

## What goes into the calendar

- Each booking marked `upcoming` becomes an event with its own stable ID.
  Regular lessons appear as the individual occurrences in the snapshot.
  Cancelled and failed-payment entries are excluded.
- Add `--include-pending` to either command to include tentative requests.
  These appear as free time in calendars that honour iCalendar transparency.
- Event summaries use `Student name – Subject` when both are available. Add
  `--hide-student-names` for an export without names. The export excludes
  classroom links, contact details and billing fields.
- Dates without a UTC offset are interpreted as `Europe/London`. Use
  `--source-timezone` if your snapshot's dates use another time zone.

The input can be a Sherpa bookings object keyed by booking reference, as in
`PII.json`, or an array of Sherpa booking objects. It uses `meetingDateTime`
(falling back to `when`) for the start, and `until` or `duration` in hours for
the end. Keep raw booking files local. Git ignores `PII.json` and generated
`.ics` files.
