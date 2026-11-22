# Sherpa iCal Automation

Turn a Sherpa Online bookings snapshot into an iCalendar file for Apple Calendar,
Google Calendar, Outlook, or another app that accepts `.ics`.

## Export a file

Run from this project directory with Python 3.14 or later. On Windows, install
the project first so Python has the time-zone data it needs.

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

## Windows setup

With Python 3.14, uv and Google Chrome installed, run these commands in
PowerShell from the project directory:

```powershell
uv sync --extra sync
.\.venv\Scripts\sherpa-ical-automation.exe prepare-chrome
.\.venv\Scripts\sherpa-ical-automation.exe install-windows
```

Open `chrome://extensions` in the Chrome profile signed into Sherpa. Turn on
**Developer mode**, click **Load unpacked**, and select the
`.sherpa/chrome-extension` folder printed by `prepare-chrome`.

Windows Task Scheduler starts the local feed at sign-in and queues a scan at
the top of each hour. Leave the signed-in Chrome profile open to process each
scan as it comes in. If it is closed, Chrome picks up the pending scan next time
you open that profile. The task names start with **Sherpa Calendar Feed** and
**Sherpa Calendar Scan**.

Subscribe to `http://127.0.0.1:8765/sherpa.ics` in a desktop calendar that
can read a local feed. In Thunderbird, choose **New Calendar → On the
Network**, select **This location doesn't require credentials**, and enter the
URL. For a one-time import in Outlook or Google Calendar, export from the
latest snapshot with the following command.
[Thunderbird subscription guide](https://support.mozilla.org/en-US/kb/creating-new-calendars)

```powershell
.\.venv\Scripts\sherpa-ical-automation.exe export --source .sherpa\bookings.json
```

### Windows-to-iPhone sync

To publish from Windows to a private iCloud calendar, enable iCloud Calendar
on your Apple Account and create an Apple app-specific password. Then run:

```powershell
uv sync --extra sync --extra icloud-windows
.\.venv\Scripts\sherpa-ical-automation.exe setup-icloud
```

Enter your Apple Account email and app-specific password at the prompts.
The password is stored in Windows Credential Manager. Setup creates
**Sherpa Lessons (Windows)** in iCloud and adds a Windows background task to
publish changed bookings. On your iPhone, select that calendar under
**Calendar → Calendars**. Choose one computer to publish your Sherpa bookings
if you use the automation on both Mac and Windows.
[Apple app-specific passwords](https://support.apple.com/en-gb/102654)

## Run the free hourly Mac automation

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

The installer starts the feed in the background and schedules scans **at the
top of every hour** in the Mac's time zone. The jobs run while you are logged
into macOS. A scheduled run missed during sleep can run when the Mac wakes.
Each successful scan compares the lessons with the saved snapshot and
updates the feed when the lesson details change. The feed keeps showing the
most recent good snapshot if Sherpa is unreachable or the sign-in expires.
[macOS scheduling](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)

The installed schedule is in
`~/Library/LaunchAgents/local.sherpa-ical-automation.scan.plist`, under
`StartCalendarInterval`. To change it, edit the interval in
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

Use `sync --include-pending` to show pending requests in the local subscription
as tentative, free events. Hourly scans keep that preference. Use
`sync --exclude-pending` to return to confirmed lessons only.

### Mac-to-iPhone sync

With iCloud Calendar enabled on your Mac, run
`.venv/bin/sherpa-ical-automation setup-icloud`. This requires macOS 14 or later
and Apple’s Command Line Tools. When prompted, grant **Sherpa iCloud Sync**
Full Access to Calendar so it can reconcile its lessons in **Sherpa Lessons
(iCloud)**. The Mac checks for changes to the booking snapshot and publishes
them to this calendar in the background. It uses the same pending-booking
preference as the local feed.

On your iPhone, enable **iCloud Calendar** for the same Apple Account and
select **Sherpa Lessons (iCloud)** in Calendar’s calendar list.
[iCloud Calendar settings](https://support.apple.com/en-gb/guide/icloud/mme4d73a8727/icloud)

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

The macOS job queues a scan at the **top of every hour**. Chrome picks it up
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
- Add `--include-pending` to `export` or `serve` to include tentative requests.
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
