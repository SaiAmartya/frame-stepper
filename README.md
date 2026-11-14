# Frame Stepper: frame-by-frame viewer for slow-motion physics lab videos

Opens a video, shows every frame with its frame number and its timestamp, and lets you
step one frame at a time. You mark the release frame and the landing frame, and it works out
Δt = END frame's timestamp − START frame's timestamp.

## How the time is measured

Every frame in a video file carries its own timestamp, which is when the camera captured
it. The program reads that timestamp for each frame and shows it to 0.00001 s, measured
from the first frame of the file. Δt is the difference between the two marked frames'
timestamps.

It does **not** use Δframes ÷ fps. iPhone slo-mo frames are often not evenly spaced: a
"240 fps" clip can mix gaps of 1/240 s and 1/120 s and average about 167 fps, so counting
frames and dividing by 240 can be wrong by a few hundredths of a second. Frame numbers are
only there to identify frames.

Δt is worked out from the unrounded timestamps, so it can differ by 0.00001 s from
subtracting the two displayed times by hand.

## Setup (one time)

Requires Python 3 with Tkinter. Clone this repo, then from its folder run:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python frame_stepper.py
```

A file picker opens in Downloads, which is where AirDrop saves files. You can also pass
the file directly:
`.venv/bin/python frame_stepper.py ~/Downloads/IMG_1234.MOV`

Frames are numbered **from 0**, so the first frame of the file is Frame 0. The program
decodes every frame once, in order, and keeps them in memory, so frame N is exactly the Nth
frame. It never guesses by seeking. You can start stepping while it is still loading.

## Keys

| Key | Action |
|-|-|
| → / ← | next / previous frame |
| Shift + → / ← | jump 10 frames |
| ↑ / ↓ | jump 100 frames |
| Home / End (fn + ← / →) | first / last frame |
| slider, or G then type a number + Return | jump to a frame |
| **S** | mark START (the frame where the cup is released) |
| **E** | mark END (the first frame where the cup touches the floor) |
| [ / ] | go back to the START / END mark |
| X | clear the marks |
| **Return** | save the trial to CSV (it also prints in Terminal) |
| P | export the current frame as a full-resolution labelled PNG |
| Space | slow playback, R rotate, Q quit |

Saved trials go to `<video name>_trials.csv`, next to the video, with these columns: trial,
video, start_frame, end_frame, start_time_s, end_time_s, delta_t_s, and saved_at. Here
delta_t_s = end_time_s − start_time_s. PNGs are saved as `<video name>_frame00123.png` in
the same place.

A trials CSV made by an older version (where delta_t_s was Δframes ÷ fps) is converted the
next time you save a trial: its timestamp-based Δt is kept as delta_t_s, and the untouched
original is copied to `<video name>_trials_old_format.csv`.

## Important: send the ORIGINAL slo-mo from the iPhone

A normal AirDrop of a slo-mo video can send a re-rendered copy instead. That copy plays
at about 30 fps with the slow part stretched out, so its timestamps are playback times,
not the real capture times, and Δt would be wrong. To get the original file, go to
**Share → Options → turn on "All Photos Data"** before you AirDrop. On a Mac with iCloud
Photos, you can also use File → Export → Export Unmodified Original.

When a video finishes loading, the program prints its frame count and the spacing between
frames. If the frames are unevenly spaced it says so, and that is fine, because Δt uses
the timestamps. If the file looks like a ~30 fps re-rendered copy rather than a high-speed
original, you get a warning.

## Built-in Mac option (no frame numbers)

You can also step through frames in QuickTime Player. Pause the video, then press → or ←
to move one frame at a time. Option + ← / → jumps to the start or end. QuickTime does not
show frame numbers or per-frame timestamps, and counting steps assumes evenly spaced
frames. That is why this tool exists.
