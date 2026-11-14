#!/usr/bin/env python3
"""
Frame Stepper - frame-by-frame viewer for slow-motion lab videos.

Every frame is decoded once, in order, and kept in memory, so frame N is
always the Nth frame of the video (no inaccurate seeking). Frames are
numbered from 0 (the first frame of the file is "Frame 0").

Times come from the timestamp stored in the file for each frame, so
Δt = (END frame's timestamp) - (START frame's timestamp). No frame rate is
assumed: iPhone slo-mo frames are often not evenly spaced, which makes
"frames ÷ fps" wrong.

Usage:
    .venv/bin/python frame_stepper.py                 # opens a file picker
    .venv/bin/python frame_stepper.py path/to/video.MOV

Keys:
    Right / Left          next / previous frame
    Shift + Right / Left  +10 / -10 frames
    Up / Down             +100 / -100 frames
    Home / End            first / last frame   (MacBook: fn+Left / fn+Right)
    S / E                 mark START (release) / END (landing) at this frame
    [ / ]                 jump to the START / END mark
    X                     clear both marks
    Return                save this trial (start/end frame and time, Δt) to CSV
    P                     export the current frame as a labelled PNG
    Space                 play / pause (slow playback)
    R                     rotate the view 90 degrees
    G                     type a frame number to jump to
    Q or Esc              quit
"""

import argparse
import csv
import datetime as _dt
import io
import shutil
import statistics
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import av
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"Missing package ({exc.name}). Run the setup command first:\n"
        "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

VIDEO_TYPES = [
    ("Videos", "*.mov *.MOV *.mp4 *.MP4 *.m4v *.M4V"),
    ("All files", "*"),
]
FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
CSV_FIELDS = ["trial", "video", "start_frame", "end_frame", "start_time_s",
              "end_time_s", "delta_t_s", "saved_at"]
# Below this frame rate a file is not a high-speed capture (e.g. the ~30 fps
# re-rendered copy an iPhone can share instead of the original slo-mo).
MIN_SLOMO_FPS = 100.0


def upgrade_row(old):
    """Convert a CSV row saved by an older version to CSV_FIELDS. Older files
    stored frames ÷ fps as delta_t_s and the timestamp difference as
    file_timestamp_delta_t_s; only the timestamp difference is kept."""
    new = {k: old.get(k) or "" for k in CSV_FIELDS}
    if "file_timestamp_delta_t_s" in old:
        new["delta_t_s"] = old["file_timestamp_delta_t_s"] or ""
    return new


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------
def load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


_font_cache = {}


def draw_label(img, lines, colors=None):
    """Draw a high-contrast text box in the top-left corner of a PIL image."""
    img = img.convert("RGB")
    size = max(14, round(min(img.size) * 0.035))
    font = _font_cache.get(size) or _font_cache.setdefault(size, load_font(size))
    draw = ImageDraw.Draw(img, "RGBA")
    pad = max(6, size // 3)
    gap = max(2, size // 5)
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    width = max(b[2] - b[0] for b in boxes) + 2 * pad
    height = sum(b[3] - b[1] for b in boxes) + gap * (len(lines) - 1) + 2 * pad
    draw.rectangle([0, 0, width, height + pad // 2], fill=(0, 0, 0, 185))
    y = pad
    for i, (line, box) in enumerate(zip(lines, boxes)):
        color = (colors[i] if colors and i < len(colors) else None) or (255, 255, 0)
        draw.text((pad - box[0], y - box[1]), line, font=font, fill=color)
        y += (box[3] - box[1]) + gap
    return img


def rotate_img(img, degrees_ccw):
    d = degrees_ccw % 360
    if d == 90:
        return img.transpose(Image.Transpose.ROTATE_90)
    if d == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if d == 270:
        return img.transpose(Image.Transpose.ROTATE_270)
    return img


# --------------------------------------------------------------------------
# Video decoding / cache
# --------------------------------------------------------------------------
class VideoCache:
    """Decodes every frame of a video in order (background thread) and keeps a
    JPEG copy of each frame, scaled to fit the screen, in memory."""

    def __init__(self, path, max_w=1600, max_h=900, quality=90):
        self.path = str(path)
        self.max_w, self.max_h, self.quality = max_w, max_h, quality
        self.frames = []      # JPEG bytes, index = frame number
        self.times = []       # seconds since the first frame (from file timestamps)
        self.pts = []         # raw timestamps (used for full-resolution export)
        self.estimated_times = False   # True if a frame had no timestamp
        self.done = False
        self.error = None
        self._stop = False

        with av.open(self.path) as c:
            s = c.streams.video[0]
            cc = s.codec_context
            self.codec = cc.name
            self.width, self.height = cc.width, cc.height
            self.nominal_fps = float(s.average_rate) if s.average_rate else None
            self.guessed_fps = float(s.guessed_rate) if s.guessed_rate else None
            self.stream_frames = s.frames or None
            if self.stream_frames and self.stream_frames > 5000:
                self.quality = min(self.quality, 75)   # long clip: keep memory down
            self.duration = (
                float(s.duration * s.time_base) if s.duration else
                (c.duration / av.time_base if c.duration else None)
            )
            self.rotation = 0
            for frame in c.decode(s):          # first frame -> rotation metadata
                self.rotation = int(getattr(frame, "rotation", 0) or 0)
                break
        # Size of the stored (display) frames, after rotation.
        rw, rh = (self.height, self.width) if self.rotation % 180 else (self.width, self.height)
        scale = min(1.0, max_w / rw, max_h / rh)
        self.disp_w = max(2, int(rw * scale) // 2 * 2)
        self.disp_h = max(2, int(rh * scale) // 2 * 2)
        # Decode size before rotation
        self.dec_w, self.dec_h = (
            (self.disp_h, self.disp_w) if self.rotation % 180 else (self.disp_w, self.disp_h)
        )

    # ---- decoding --------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    def _run(self):
        try:
            self.decode_all()
        except Exception as exc:  # report to the UI
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.done = True

    def decode_all(self):
        with av.open(self.path) as c:
            s = c.streams.video[0]
            s.thread_type = "AUTO"
            t0 = None
            for i, frame in enumerate(c.decode(s)):
                if self._stop:
                    return
                t = frame.time
                if t is None:
                    self.estimated_times = True
                    fps = self.nominal_fps or 240.0
                    t = i / fps
                if t0 is None:
                    t0 = t
                img = frame.reformat(width=self.dec_w, height=self.dec_h,
                                     format="rgb24").to_image()
                img = rotate_img(img, self.rotation)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=self.quality)
                # append pts/times first so frames[i] always has metadata
                self.pts.append(frame.pts)
                self.times.append(t - t0)
                self.frames.append(buf.getvalue())

    def image(self, i):
        return Image.open(io.BytesIO(self.frames[i]))

    def full_res_image(self, i):
        """Decode frame i at full resolution, located by its exact timestamp.
        Falls back to the in-memory copy if that fails."""
        target = self.pts[i]
        if target is not None:
            try:
                with av.open(self.path) as c:
                    s = c.streams.video[0]
                    c.seek(target, stream=s, backward=True, any_frame=False)
                    for frame in c.decode(s):
                        if frame.pts == target:
                            return rotate_img(frame.to_image(), self.rotation), True
                        if frame.pts is not None and frame.pts > target:
                            break
            except Exception:
                pass
        return self.image(i), False

    # ---- timing analysis -------------------------------------------------
    def timing_report(self):
        """Return (lines, warnings, summary) describing the frame timing."""
        n = len(self.times)
        lines = [f"Frames decoded: {n}   (numbered 0 to {n - 1})"]
        warns, summary = [], ""
        if self.estimated_times:
            warns.append("Some frames in this file have no timestamp, so their times were "
                         "estimated from the nominal frame rate. Δt may be wrong.")
        d = [b - a for a, b in zip(self.times, self.times[1:])]
        d = [x for x in d if x > 0]
        if d:
            med = statistics.median(d)
            med_fps = 1.0 / med
            avg_fps = len(d) / sum(d)
            off = sum(1 for x in d if abs(x - med) > 0.1 * med)
            lines.append(
                f"Frame spacing from timestamps: median {med * 1000:.3f} ms "
                f"({med_fps:.1f} fps), min {min(d) * 1000:.3f} ms, "
                f"max {max(d) * 1000:.3f} ms; average {avg_fps:.1f} fps"
            )
            if off > max(2, 0.01 * len(d)):
                lines.append(f"Frames are UNEVENLY spaced: {off} of {len(d)} gaps differ "
                             ">10% from the median. That is fine: Δt is the difference "
                             "of the two marked frames' own timestamps, not frames ÷ fps.")
                summary = (f"Frames are unevenly spaced (average {avg_fps:.0f} fps). "
                           "That is fine: Δt uses each frame's own timestamp.")
            else:
                lines.append("Frames are evenly spaced.")
                summary = f"{med_fps:.1f} fps, evenly spaced."
            if med_fps < MIN_SLOMO_FPS:
                warns.append(
                    f"Most frames in this file are {med * 1000:.1f} ms apart (about "
                    f"{med_fps:.0f} fps), so it is not a high-speed capture. If it was "
                    "filmed in slo-mo, this is probably a re-rendered copy: its "
                    "timestamps are playback times, not real times, so Δt would be "
                    "wrong. Re-send it from the iPhone with AirDrop > Options > 'All "
                    "Photos Data' ON (or export the Unmodified Original)."
                )
        return lines, warns, summary


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root, path, csv_path=None, play_fps=30, max_size=None):
        self.root = root
        self.path = Path(path)
        self.play_fps = play_fps
        self.idx = 0
        self.start_mark = None
        self.end_mark = None
        self.extra_rot = 0
        self.playing = False
        self._pending = False
        self._reported = False
        self._photo = None

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        max_w, max_h = max(320, sw - 60), max(240, sh - 300)
        if max_size:
            max_w = max_h = max_size
        self.cache = VideoCache(self.path, max_w=max_w, max_h=max_h)
        self.csv_path = Path(csv_path) if csv_path else self.path.with_name(
            self.path.stem + "_trials.csv")

        root.title(f"Frame Stepper - {self.path.name}")
        root.configure(bg="black")
        self._build_ui()
        self._bind_keys()
        self._print_header()
        self.cache.start()
        self.root.after(100, self._poll)

    # ---- UI --------------------------------------------------------------
    def _build_ui(self):
        r = self.root
        self.view = tk.Label(r, bg="black", fg="white", text="Loading first frames...",
                             takefocus=1)
        self.view.pack(side="top")

        self.slider = tk.Scale(r, from_=0, to=0, orient="horizontal", showvalue=False,
                               takefocus=0, command=self._on_slider, length=600)
        self.slider.pack(side="top", fill="x", padx=8)
        self.slider.bind("<ButtonRelease-1>", lambda e: self.view.focus_set())

        bar = tk.Frame(r)
        bar.pack(side="top", fill="x", padx=8, pady=2)
        self.info = tk.Label(bar, text="", font=("Menlo", 13), anchor="w", justify="left",
                             wraplength=max(500, self.cache.disp_w - 160))
        self.info.pack(side="left", fill="x", expand=True)

        tk.Label(bar, text="  go to:").pack(side="left")
        self.goto_var = tk.StringVar()
        self.goto_entry = tk.Entry(bar, textvariable=self.goto_var, width=7)
        self.goto_entry.pack(side="left")
        self.goto_entry.bind("<Return>", self._apply_goto)

        btns = tk.Frame(r)
        btns.pack(side="top", fill="x", padx=8, pady=(0, 2))
        for text, cmd in [("Mark START (S)", self.mark_start), ("Mark END (E)", self.mark_end),
                          ("Clear marks (X)", self.clear_marks),
                          ("Save trial to CSV (Return)", self.save_trial),
                          ("Export PNG (P)", self.export_png)]:
            tk.Button(btns, text=text, command=cmd, takefocus=0).pack(side="left", padx=2)
        self.status = tk.Label(r, text="", anchor="w", font=("Menlo", 12), fg="#0a7")
        self.status.pack(side="top", fill="x", padx=8)
        tk.Label(r, anchor="w", justify="left", font=("Menlo", 11), fg="#666", text=(
            "←/→ 1 frame   Shift+←/→ 10   ↑/↓ 100   Home/End (fn+←/→)\n"
            "S/E mark start/end   [ ] jump to mark   Return save trial   P png\n"
            "Space play   R rotate   G go to frame   Q quit"
        )).pack(side="top", fill="x", padx=8, pady=(0, 6))
        self.view.focus_set()

    def _bind_keys(self):
        r = self.root
        steps = {"<Right>": 1, "<Left>": -1, "<Shift-Right>": 10, "<Shift-Left>": -10,
                 "<Up>": 100, "<Down>": -100, "<period>": 1, "<comma>": -1}
        for seq, step in steps.items():
            r.bind(seq, self._key(lambda s=step: self.step(s)))
        for seq in ("<Home>", "<Command-Left>"):
            r.bind(seq, self._key(lambda: self.goto(0)))
        for seq in ("<End>", "<Command-Right>"):
            r.bind(seq, self._key(lambda: self.goto(10 ** 9)))
        letter = {"s": self.mark_start, "e": self.mark_end, "x": self.clear_marks,
                  "p": self.export_png, "r": self.rotate, "g": self.focus_goto,
                  "q": self.quit}
        for ch, fn in letter.items():
            r.bind(f"<KeyPress-{ch}>", self._key(fn))
            r.bind(f"<KeyPress-{ch.upper()}>", self._key(fn))
        r.bind("<bracketleft>", self._key(lambda: self.start_mark is not None and self.goto(self.start_mark)))
        r.bind("<bracketright>", self._key(lambda: self.end_mark is not None and self.goto(self.end_mark)))
        r.bind("<Return>", self._key(self.save_trial))
        r.bind("<KP_Enter>", self._key(self.save_trial))
        r.bind("<space>", self._key(self.toggle_play))
        r.bind("<Escape>", lambda e: self.view.focus_set() if isinstance(e.widget, tk.Entry) else self.quit())
        r.protocol("WM_DELETE_WINDOW", self.quit)

    def _key(self, fn):
        def handler(event):
            if isinstance(event.widget, tk.Entry):   # typing in a text box
                return None
            fn()
            return "break"
        return handler

    # ---- navigation ------------------------------------------------------
    @property
    def loaded(self):
        return len(self.cache.frames)

    def goto(self, i):
        if self.loaded == 0:
            return
        self.idx = max(0, min(int(i), self.loaded - 1))
        self._request_render()

    def step(self, n):
        self.goto(self.idx + n)

    def _on_slider(self, value):
        v = int(float(value))
        if v != self.idx:
            self.goto(v)

    def focus_goto(self):
        self.goto_entry.focus_set()
        self.goto_entry.select_range(0, "end")

    def _apply_goto(self, _event=None):
        try:
            self.goto(int(self.goto_var.get().strip()))
        except ValueError:
            self.flash("Type a whole frame number, e.g. 120")
        self.view.focus_set()
        return "break"

    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            self._play_tick()

    def _play_tick(self):
        if not self.playing:
            return
        if self.idx >= self.loaded - 1:
            self.playing = False
            return
        self.step(1)
        self.root.after(max(1, int(1000 / self.play_fps)), self._play_tick)

    def rotate(self):
        self.extra_rot = (self.extra_rot - 90) % 360   # clockwise
        self._request_render()

    # ---- marks / trials --------------------------------------------------
    def mark_start(self):
        self.start_mark = self.idx
        self._request_render()

    def mark_end(self):
        self.end_mark = self.idx
        self._request_render()

    def clear_marks(self):
        self.start_mark = self.end_mark = None
        self._request_render()

    def delta(self):
        """Δt in seconds = END frame's timestamp - START frame's timestamp,
        or None until both marks are set."""
        if self.start_mark is None or self.end_mark is None:
            return None
        return self.cache.times[self.end_mark] - self.cache.times[self.start_mark]

    def _mark_text(self, mark):
        if mark is None:
            return "-"
        return f"frame {mark} @ {self.cache.times[mark]:.5f} s"

    def save_trial(self):
        dt = self.delta()
        if dt is None:
            self.flash("Mark START (S) and END (E) first.")
            return
        t_start = self.cache.times[self.start_mark]
        t_end = self.cache.times[self.end_mark]
        rows, header, note = [], None, ""
        try:
            if self.csv_path.exists():
                with open(self.csv_path, newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    header = reader.fieldnames
            trial = len(rows) + 1
            row = {
                "trial": trial,
                "video": self.path.name,
                "start_frame": self.start_mark,
                "end_frame": self.end_mark,
                "start_time_s": f"{t_start:.5f}",
                "end_time_s": f"{t_end:.5f}",
                "delta_t_s": f"{dt:.5f}",
                "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
            }
            if header == CSV_FIELDS:
                with open(self.csv_path, "a", newline="") as f:
                    csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
            else:
                if header:   # CSV from an older version: keep a copy, then convert
                    backup = self.csv_path.with_name(
                        self.csv_path.stem + "_old_format.csv")
                    if not backup.exists():
                        shutil.copy2(self.csv_path, backup)
                    rows = [upgrade_row(r) for r in rows]
                    note = f"  (older CSV converted; copy kept as {backup.name})"
                tmp = self.csv_path.with_name(self.csv_path.name + ".tmp")
                with open(tmp, "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                    w.writeheader()
                    w.writerows(rows + [row])
                tmp.replace(self.csv_path)
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            self.flash(f"Could not save to CSV: {exc}", "#c00")
            return
        msg = (f"Trial {trial}: START frame {self.start_mark} @ {t_start:.5f} s, "
               f"END frame {self.end_mark} @ {t_end:.5f} s, "
               f"Δt = {dt:.5f} s (END time − START time)")
        print(msg + f"   -> {self.csv_path}{note}", flush=True)
        self.flash(f"Saved. {msg}{note}")

    def export_png(self):
        if self.loaded == 0:
            return
        i = self.idx
        self.flash("Exporting full-resolution frame...")
        self.root.update_idletasks()
        img, full = self.cache.full_res_image(i)
        img = rotate_img(img, self.extra_rot)
        lines, colors = self._label_lines(i, include_delta=False)
        img = draw_label(img, lines, colors)
        out = self.path.with_name(f"{self.path.stem}_frame{i:05d}.png")
        try:
            img.save(out)
        except OSError:
            out = Path.cwd() / out.name
            img.save(out)
        note = "" if full else " (screen-size copy)"
        print(f"Exported frame {i}{note}: {out}", flush=True)
        self.flash(f"Exported frame {i}{note}: {out}")

    # ---- rendering -------------------------------------------------------
    def _label_lines(self, i, include_delta=True):
        n = self.loaded
        total = n if self.cache.done else f"{n}+"
        lines = [f"Frame {i}  (0-based, of {total})",
                 f"t = {self.cache.times[i]:.5f} s (file timestamp)"]
        colors = [None, None]
        tags = []
        if i == self.start_mark:
            tags.append("START mark")
        if i == self.end_mark:
            tags.append("END mark")
        if tags:
            lines.append(" + ".join(tags))
            colors.append((0, 255, 120))
        if include_delta:
            for name, mark in (("START", self.start_mark), ("END  ", self.end_mark)):
                if mark is not None:
                    lines.append(f"{name} {self._mark_text(mark)}")
                    colors.append((120, 200, 255))
            dt = self.delta()
            if dt is not None:
                lines.append(f"Δt = {dt:.5f} s")
                colors.append((120, 200, 255))
        return lines, colors

    def _request_render(self):
        if not self._pending:
            self._pending = True
            self.root.after_idle(self._render)

    def _render(self):
        self._pending = False
        if self.loaded == 0:
            return
        i = self.idx
        img = rotate_img(self.cache.image(i), self.extra_rot)
        lines, colors = self._label_lines(i)
        img = draw_label(img, lines, colors)
        self._photo = ImageTk.PhotoImage(img)
        self.view.configure(image=self._photo, text="")
        if int(float(self.slider.get())) != i:
            self.slider.set(i)
        self._update_info()

    def _update_info(self):
        if self.loaded == 0:
            return
        txt = (f"Frame {self.idx}   t = {self.cache.times[self.idx]:.5f} s"
               f"   |   START {self._mark_text(self.start_mark)}"
               f"   END {self._mark_text(self.end_mark)}")
        dt = self.delta()
        if dt is not None:
            txt += f"   |   Δt = {dt:.5f} s (END time − START time)"
        self.info.configure(text=txt)

    def flash(self, msg, color="#0a7"):
        self.status.configure(text=msg, fg=color)

    # ---- loading progress ------------------------------------------------
    def _poll(self):
        c = self.cache
        n = self.loaded
        if n:
            self.slider.configure(to=max(0, n - 1))
            if self._photo is None:
                self._request_render()
        if not c.done:
            est = f" of ~{c.stream_frames}" if c.stream_frames else ""
            mb = sum(map(len, c.frames[-50:])) / max(1, min(50, n)) * n / 1e6 if n else 0
            self.flash(f"Decoding frames... {n}{est} loaded ({mb:.0f} MB). "
                       "You can start stepping already.", "#a60")
            self.root.after(150, self._poll)
            return
        if c.error and n == 0:
            messagebox.showerror("Could not read video", c.error)
            self.quit()
            return
        if not self._reported:
            self._reported = True
            self._request_render()
            lines, warns, summary = c.timing_report()
            print("\n".join("  " + x for x in lines), flush=True)
            for w in warns:
                print("  WARNING: " + w, flush=True)
            if c.error:
                print(f"  WARNING: decoding stopped early: {c.error}", flush=True)
            msg = f"Loaded {n} frames (0-{n - 1})."
            if warns:
                self.flash(msg + "  WARNING: " + warns[-1], "#c00")
                messagebox.showwarning("Check the video", "\n\n".join(warns))
                self.view.focus_set()
            else:
                self.flash(f"{msg}  {summary}")

    def _print_header(self):
        c = self.cache
        print(f"\nVideo: {self.path}", flush=True)
        print(f"  codec {c.codec}, {c.width}x{c.height}, rotation {c.rotation} deg, "
              f"shown at {c.disp_w}x{c.disp_h}", flush=True)
        nf = f"{c.nominal_fps:.3f}" if c.nominal_fps else "?"
        dur = f"{c.duration:.3f} s" if c.duration else "?"
        print(f"  average frame rate (header) {nf} fps, frames (header) "
              f"{c.stream_frames or '?'}, duration {dur}", flush=True)
        print("  Δt = END frame's timestamp − START frame's timestamp "
              "(read from the file; no frame rate is assumed)", flush=True)
        print(f"  trials CSV: {self.csv_path}", flush=True)

    def quit(self):
        self.playing = False
        self.cache.stop()
        self.root.destroy()


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Frame-by-frame viewer with frame numbers.")
    ap.add_argument("video", nargs="?", help="video file (omit to pick one)")
    ap.add_argument("--fps", type=float, help=argparse.SUPPRESS)   # no longer used
    ap.add_argument("--csv", help="CSV file for saved trials (default: <video>_trials.csv)")
    ap.add_argument("--play-fps", type=float, default=30.0, help="Space-bar playback speed")
    ap.add_argument("--max-size", type=int,
                    help="max on-screen width/height in pixels (default: fit the screen)")
    args = ap.parse_args(argv)
    if args.fps is not None:
        print("Note: --fps is ignored. Δt now comes from each frame's own timestamp.")

    root = tk.Tk()
    path = args.video
    if not path:
        root.withdraw()
        start = Path.home() / "Downloads"
        path = filedialog.askopenfilename(
            title="Choose the slow-motion video", filetypes=VIDEO_TYPES,
            initialdir=str(start if start.is_dir() else Path.home()))
        if not path:
            print("No video selected.")
            return 1
        root.deiconify()
    path = Path(path).expanduser()
    if not path.is_file():
        print(f"File not found: {path}")
        return 1
    try:
        App(root, path, csv_path=args.csv, play_fps=args.play_fps,
            max_size=args.max_size)
    except av.error.FFmpegError as exc:
        messagebox.showerror("Could not open video", str(exc))
        return 1
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
