# highlightarr

Watches folders for finished recordings (MKV, MP4, TS and other common formats), keeps only the interesting parts
and writes a shorter "highlights" version next to your library — no manual editing.

It finds the moments by looking at the commentary: speech peaks that stand out
from the local average are kept (with some padding), plus a fixed intro and outro.
Cuts are made with ffmpeg, either by stream copy (fast, snaps to keyframes) or by
re-encoding (clean cuts, slower).

## Concepts

- **Profile** – the cutting rules for one kind of content (Racing, Soccer, a movie…):
  target length, what to skip at the start/end, how much to keep around a moment,
  preferred audio language, stream copy vs. re-encode, delete the original or not.
  See [Profile options](#profile-options).
- **Workflow** – picks up files from a *source folder*, processes them with a
  profile and writes `<name> - Highlights.mkv` to a *destination folder*. The destination may
  be the source folder itself (the highlights then sit next to the originals): files ending in
  ` - Highlights` are never picked up again.
- **Activity** – the outcome of every file (success or failure, with the reason).

A file is only picked up once its size has been unchanged for the *settle time*,
so recordings that are still being written or copied are left alone. Output is
written to `*.part` first and renamed when it looks right, so a half-finished
file never shows up in your library.

## Supported formats

| Picked up | Not supported |
|---|---|
| `.mkv` `.mp4` `.m4v` `.mov` `.avi` `.ts` `.m2ts` `.mts` `.webm` `.wmv` `.flv` `.mpg` `.mpeg` `.3gp` | `.vob` `.iso` `.ogv` `.divx` `.rm` `.rmvb` |

- The highlights file is **always a Matroska file** (`<name> - Highlights.mkv`), whatever the source
  format. Matroska takes almost any video and audio codec, which keeps stream copy working
  for all of the formats above.
- Recordings from a TV receiver (`.ts`) often start hours into their own timeline; this is
  handled. Teletext and other subtitle formats Matroska cannot hold are left out, MP4 text
  subtitles are converted to SRT, and all audio tracks are kept.
- `.vob` is left out on purpose: DVD folders are split into menu and chunk files that do not make
  sense as separate recordings. Convert or join them first.
- Stream copy cuts at keyframes, so formats with sparse keyframes (some old AVI, MPEG and FLV
  files) can give clips that start a little early or late; use **Re-encode** for exact cuts.

## Which files get picked up

Open **Workflows → Files** (also linked from a workflow's edit page) to see every file in a
workflow's source folder, its status, and the reason in plain words. It uses the same rules as
the worker, so what it says is what will happen.

| Status | Meaning |
|---|---|
| processing | The file is being cut right now. |
| ready | It has settled and will be processed at the next scan (or after the current file). |
| waiting | It is not picked up yet: it has not been checked, its size is still changing, or it has not stayed unchanged for the settle time. |
| done | Processed already. It is not processed again unless it is removed and added back, or the app restarts. |
| failed | Processing failed (the reason is shown). It is not retried automatically. Fix the problem, then press **Retry** on its row, or **Retry all failed** at the top, and it is tried again at the next scan. |
| skipped | Ignored on purpose: a highlights file for it already exists, its format is not supported, it is hidden, or it is itself a highlights file. |

Other reasons a file is not picked up, shown at the top of the page: the workflow is disabled, the
source folder is missing or not mounted, or the file is in a subfolder while "Scan subfolders too"
is off. Folders starting with `.` or `@` and folders named `#recycle` are never scanned, and files
are processed one at a time. The app keeps this state in memory only, so a restart makes it forget
what it has handled: failed files are tried again, and files without a highlights file are processed again.

## Default profiles

A new installation starts with these profiles. Pick one in a workflow, or copy its numbers into your own.

| Profile | For | How it cuts |
|---|---|---|
| Movies | Films | About 12% of the film. The last 7 minutes (credits) are ignored, the first and last minute are always kept, and clips of about 35 seconds are at least 2 minutes apart. Re-encode. |
| TV shows | Episodes | 25% of the episode. 90 seconds of end credits are ignored, and the first and last 45 seconds are kept, with clips of about 35 seconds. Re-encode. |
| News | Bulletins | 25% of the bulletin: the opening 90 seconds (the headlines) and the last 30 seconds, plus clips of about 30 seconds at least 2 minutes apart, so items are spread over the broadcast. Re-encode. |
| Sport | Any sport | 20%, with 90 seconds kept at the start and the end and clips of about 50 seconds. Re-encode. |
| Racing | Races | 25%, with 150 seconds kept at the start and 120 seconds at the end. Stream copy. |
| Soccer | Football matches | 15%, with 60 seconds at the start and the end and clips of about 50 seconds. Stream copy. |

Highlights are found by listening for moments where the speech stands out from the minutes around it. That
works best for sport, where commentary rises at the key moments. In films and series it picks the most intense
scenes, and in the news mostly the headlines plus a spread of items. Treat the defaults as a starting point
and adjust them to your taste.

The profiles are created on the first start. **Profiles → Add default profiles** adds the ones that are missing
later on (profiles you already have, even with the same name, are never changed).

## Profile options

A profile tells highlightarr how to turn one recording into a highlights file. The
recording is first listened to: seconds where the commentary is louder than the
surrounding minutes score as "moments". The loudest moments are kept, each with a bit
of padding, until the target length is reached, plus a fixed intro and outro.

**What to keep**

| Option | What it does |
|---|---|
| Target length | The wanted length as a fraction of the recording that remains after the two trims below. `0.25` means roughly a quarter. If the result ends up less than half or more than 1.5 times the target, it is rejected and the original is left alone. |
| Skip at start (seconds) | Ignore this much at the beginning, such as a pre-show or pre-race programme. Nothing from it is kept. |
| Trim at end (seconds) | Ignore this much at the end, such as the post-show or podium talk. |
| Always keep after start (seconds) | Always keep this much right after the skipped part, so the highlights open with the real start. |
| Always keep before end (seconds) | Always keep this much just before the trimmed end, so they close with the finish. |
| Clip padding before / after a moment (seconds) | How much is kept before and after each detected moment, for context. |
| Minimum gap between moments (seconds) | When two moments are closer together than this, only the louder one is used, which spreads the clips over the whole recording. |
| Audio language preference | Comma-separated language codes in order of preference (`nld,eng`). The first matching audio track is the one analysed. The result keeps all audio tracks and subtitles, with the analysed language set as the default. |
| Delete the original | Remove the source file after a valid highlights file has been written. Off by default. |

Recordings under 10 minutes are not processed, and neither are recordings where the skip, trim, intro
and outro leave no room for highlights. In both cases the Activity page says why.

**How the cuts are made**

- **Stream copy** (default): the video and audio are copied as they are and only cut and
  joined. It is very fast and keeps the original quality, but a cut can only start on a
  keyframe, so clips may begin a few seconds off and there can be a brief glitch or
  frozen frame at a join.
- **Re-encode**: the video is decoded and compressed again, so cuts are exact and joins
  are clean. It is much slower and uses a lot of CPU (expect it to take a long time on a small NAS),
  and quality drops slightly because the video is
  compressed a second time. The audio is re-encoded to AAC. When you tick Re-encode,
  more options appear:

| Option | What it does |
|---|---|
| Encoder | **Software** (x264, the default) gives the best quality for the file size but is slow. **Hardware** uses the Intel GPU through VAAPI and is much faster and easier on the CPU, but makes bigger files at the same quality. See [Hardware encoding](#hardware-encoding-intel-gpu). |
| Encoding speed | The x264 preset, from `ultrafast` to `medium`. This is the main control for how long encoding takes: faster presets finish sooner but make bigger files at the same quality. The default is `veryfast`. |
| Video quality (CRF) | From 15 to 30, default 21. Lower is better quality and a bigger file; higher is smaller and a little worse. 18 to 23 is the usual range. It has much less effect on speed than the preset. |

The speed setting only applies to the software encoder, and with the hardware encoder the quality number is used as the constant quality level (QP). All of them are ignored when Re-encode is off.

## Going easy on the NAS

Cutting a video uses a lot of CPU and disk. **Settings → Processing** keeps the rest of the NAS
responsive (changes apply to the next file, no restart needed):

| Setting | What it does |
|---|---|
| Processing priority | The nice level of ffmpeg, 0 (normal) to 19 (lowest). The default is 10, so Plex, Jellyfin and your other containers get the CPU first. Disk access is lowered as well. |
| ffmpeg threads | 0 lets ffmpeg use every core. Set 1 or 2 to leave the other cores alone; the job then takes longer. |
| Process only from / until | An optional window in local time (for example 22 to 7) in which new files are started. Outside it files wait (the Files page says so), and the window may run past midnight. A file that is already being cut when the window closes is finished first. Leave both empty to process at any time. |

## Hardware encoding (Intel GPU)

With a profile set to **Re-encode** and **Encoder: Hardware**, the video is encoded by the Intel GPU
(VAAPI, H.264) instead of the CPU. On a small NAS this is typically several times faster.

1. The image contains the Intel drivers (amd64 only, the arm64 image has none). Check that the NAS
   has a GPU device: `ls -ln /dev/dri` should list `renderD128`.
2. In `docker-compose.yml`, uncomment `devices: - /dev/dri:/dev/dri`. If the container user cannot open
   the device, also uncomment `group_add` and give the numeric group id that owns `renderD128`.
3. Recreate the container, open **About** and look at *Hardware encoding (VAAPI)*: it must say
   "available". If it says why it is not, fix that first (`docker exec highlightarr vainfo` shows what the driver sees).
4. Choose **Hardware** in the profile.

If the GPU turns out not to be usable, the file is encoded in software instead and the Activity page
says "software encoder, because …", so nothing is lost. Set `VAAPI_DEVICE` if your render device is not
`/dev/dri/renderD128`, and `LIBVA_DRIVER_NAME=i965` to try the older Intel driver if the default does not work.
Hardware encoders are less efficient than x264: expect larger files at the same quality number.

## Logs

**Logs** in the menu (`/logs`) shows what highlightarr did, newest first: when it started, which
files it processed, and why something failed, including ffmpeg's own error messages. Filter on warnings or
errors, search, refresh automatically, or download the whole log. The log is also written to
`highlightarr.log` next to the database (four files of up to 1 MB, so it survives restarts) and, as before, to
the container log. Set `LOG_PATH` to another location, or to an empty value to switch the file off.

## Run it

```yaml
services:
  highlightarr:
    image: ghcr.io/smartflowz/highlightarr:latest
    restart: unless-stopped
    init: true
    user: "1026:100"          # a user that may read the media and write the config
    ports: ["9797:9797"]        # host:container; change the left number to publish on another port
    environment:
      - TZ=Europe/Amsterdam
      - POLL_SEC=60
      - SETTLE_SEC=300
    volumes:
      - ./config:/config      # the database
      - /volume1/data/media:/media # mount one broad root; pick sub-folders in the UI
```

Open `http://<host>:9797`. See `docker-compose.yml` for the full example.

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `UTC` | Time zone for the times shown in Activity and in the logs, for example `Europe/Amsterdam`. |
| `POLL_SEC` | `60` | How often the source folders are scanned. Editable on the Settings page. |
| `SETTLE_SEC` | `300` | How long a file must stay the same size before it is processed. Editable on the Settings page. |
| `MEDIA_ROOTS` | `/media` | Comma-separated folders workflows may read from and write to. The folder picker never leaves these. |
| `PORT` | `9797` | The port the app listens on inside the container. Normally leave it and change the left side of `ports:`; set it when the container uses host networking. |
| `DB_PATH` | `/config/highlightarr.db` | SQLite database. |
| `LOG_PATH` | next to the database | The log file shown on the Logs page. Empty switches it off. |
| `FFMPEG_NICE` | `10` | Default for Settings → Processing priority (0 to 19). |
| `FFMPEG_THREADS` | `0` | Default for Settings → ffmpeg threads (0 = all cores). |
| `PROCESS_FROM`, `PROCESS_UNTIL` | empty | Default for the processing window, in hours (0 to 23). |
| `VAAPI_DEVICE` | `/dev/dri/renderD128` | The GPU device used for hardware encoding. |
| `LIBVA_DRIVER_NAME` | automatic | Force an Intel driver, for example `i965`. |

Values saved on the Settings page override the variables above, and apply without a restart.

## About page

**About** in the menu lists the version of highlightarr and of everything it runs on (Python,
FastAPI, SQLModel, SQLite, ffmpeg and more), the operating system, the user the container runs as,
the time zone, the processing settings, and where the database, log and media folders are. It also
checks that ffmpeg, ffprobe and the x264 encoder (needed for Re-encode) are installed and marks anything
missing, and reports whether hardware encoding works. Include it when
you report a problem. The build's commit id is shown when the image was built by the GitHub workflow.

## Good to know

- **No login.** Anyone who can reach the port can use the app, so keep it on your
  LAN or put it behind a reverse proxy that does authentication. Cross-site form
  posts from other web pages are refused.
- One file at a time, one process (the web UI reads the worker's live
  progress from memory, so do not run uvicorn with `--workers`).
- **Updates can be breaking.** The database is created on first start and is never migrated.
  After an update that changes it, the app fails to start or shows an error: stop the
  container, empty the config folder and start it again. Profiles, workflows and activity
  are lost, so write down your profile settings first if you want to keep them.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # needs ffmpeg/ffprobe on PATH for the integration tests
ruff check .
pytest
DB_PATH=./dev.db MEDIA_ROOTS=$PWD/media uvicorn app.main:app --reload --port 9797
```

Layout: `detector.py` decides what to keep (pure functions), `media.py` is all
ffmpeg/ffprobe I/O, `worker.py` is the background loop, `routers/` the web pages,
`forms.py` the validation rules, `paths.py` the folder picker and path safety.

## License

highlightarr is free software, licensed under the **GNU General Public License, version 3** (see the
[LICENSE](LICENSE) file). Copyright (C) 2026 smartflowz.

You may use, study, share and change it. If you distribute a modified version, you must make its source
available under the same license. ffmpeg, the Intel drivers and the Python libraries in the Docker image keep
their own licenses.
