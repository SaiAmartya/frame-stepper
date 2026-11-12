# Frame Stepper: frame-by-frame viewer for slow-motion physics lab videos

Opens a video, numbers every frame on screen, and lets you step one frame at a time.
You mark the release frame and the landing frame, and it works out
Δt = Δframes ÷ fps (240 by default).

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
the file and the fps directly:
`.venv/bin/python frame_stepper.py ~/Downloads/IMG_1234.MOV --fps 240`

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

The fps box at the bottom changes the fps used for Δt. Saved trials go to
`<video name>_trials.csv`, next to the video, with these columns: trial, start_frame, end_frame,
delta_frames, fps_used, delta_t_s, and file_timestamp_delta_t_s. PNGs are saved as
`<video name>_frame00123.png` in the same place.

## Important: send the ORIGINAL slo-mo from the iPhone

A normal AirDrop of a slo-mo video can send a re-rendered copy instead. That copy plays
at about 30 fps and the slow part is stretched out, so the frames are not reliably
1/240 s apart. To get the real 240 fps file, go to **Share → Options → turn on "All Photos
Data"** before you AirDrop. On a Mac with iCloud Photos, you can also use
File → Export → Export Unmodified Original.

When a video finishes loading, the program prints its frame rate, its frame count, and
whether the timestamps are evenly spaced. If the file does not look like a ~240 fps
original, you get a warning. A good file shows something like
"Frame rate from timestamps: 240.00 fps … evenly spaced".

## Built-in Mac option (no frame numbers)

You can also step through frames in QuickTime Player. Pause the video, then press → or ←
to move one frame at a time. Option + ← / → jumps to the start or end. QuickTime does not
show frame numbers, so you have to count the steps yourself. That is why this tool exists.
