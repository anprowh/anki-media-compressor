#!/usr/bin/env python3
"""
anki_compress_media.py — shrink an Anki profile's media folder.

  audio  : wav / flac / aiff / m4a / mp3 / ogg  ->  Opus (.ogg)  [or MP3 with --audio mp3]
  images : png / jpg / jpeg / bmp / tiff / static gif  ->  WebP

Then rewrites every [sound:...] and <img src="..."> reference in your notes
so nothing breaks. Animated GIFs, SVGs and files that would not get smaller
are left untouched.

REQUIREMENTS
  pip install pillow
  ffmpeg on PATH  (https://ffmpeg.org)   — used for audio

USAGE
  1. CLOSE ANKI.  (The script refuses to run otherwise.)
  2. python anki_compress_media.py "/path/to/Anki2/User 1" --dry-run   # preview
  3. python anki_compress_media.py "/path/to/Anki2/User 1"             # do it
  4. Open Anki -> Tools -> Check Media, then sync.

  Profile folder locations:
    Windows : %APPDATA%\\Anki2\\<profile>
    macOS   : ~/Library/Application Support/Anki2/<profile>
    Linux   : ~/.local/share/Anki2/<profile>

  Options:
    --audio opus|mp3      opus = smallest (default). Use mp3 if you rely on
                          older devices / iOS versions that can't play .ogg.
    --audio-bitrate 48k   opus: 32k-64k is plenty for speech. mp3: use 96k+.
    --webp-quality 80     0-100, lossy WebP quality.
    --max-image-size 1600 downscale longest side to this many px (0 = keep).
    --keep-originals      move originals to <profile>/media_originals_backup
                          instead of deleting them.
    --dry-run             only report what would happen.

A copy of collection.anki2 is written to <profile>/collection.anki2.pre-compress.bak
before anything is changed.
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required:  pip install pillow")

AUDIO_EXT = {".wav", ".flac", ".aiff", ".aif", ".m4a", ".mp3", ".ogg", ".oga", ".wma"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif"}
FIELD_SEP = "\x1f"


# --------------------------------------------------------------------------- helpers
def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def unique_name(media: Path, stem: str, ext: str, taken: set) -> str:
    """Return a filename that exists neither on disk nor in the planned set."""
    cand = f"{stem}{ext}"
    i = 1
    while (media / cand).exists() or cand in taken:
        cand = f"{stem}_{i}{ext}"
        i += 1
    taken.add(cand)
    return cand


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def is_animated_gif(p: Path) -> bool:
    try:
        with Image.open(p) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:
        return True  # unreadable -> leave alone


# --------------------------------------------------------------------------- converters
def convert_audio(src: Path, dst: Path, fmt: str, bitrate: str) -> bool:
    if fmt == "opus":
        codec = ["-c:a", "libopus", "-b:a", bitrate, "-vbr", "on", "-application", "voip"]
    else:
        codec = ["-c:a", "libmp3lame", "-b:a", bitrate]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-map_metadata", "-1",
           *codec, str(dst)]
    return subprocess.run(cmd).returncode == 0 and dst.exists()


def convert_image(src: Path, dst: Path, quality: int, max_side: int) -> bool:
    try:
        with Image.open(src) as im:
            if im.mode in ("P", "LA", "RGBA"):
                im = im.convert("RGBA")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if max_side and max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
            im.save(dst, "WEBP", quality=quality, method=6)
        return True
    except Exception as e:
        print(f"    ! image error {src.name}: {e}")
        return False


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", help="path to the Anki profile folder (contains collection.anki2)")
    ap.add_argument("--audio", choices=["opus", "mp3"], default="opus")
    ap.add_argument("--audio-bitrate", default=None)
    ap.add_argument("--webp-quality", type=int, default=80)
    ap.add_argument("--max-image-size", type=int, default=1600)
    ap.add_argument("--keep-originals", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.audio_bitrate is None:
        args.audio_bitrate = "48k" if args.audio == "opus" else "112k"
    audio_ext = ".ogg" if args.audio == "opus" else ".mp3"

    profile = Path(args.profile).expanduser().resolve()
    media = profile / "collection.media"
    coll = profile / "collection.anki2"
    if not media.is_dir() or not coll.is_file():
        sys.exit(f"Not an Anki profile folder: {profile}")

    # Anki holds an exclusive lock while open; a journal/WAL file is a good tell.
    for lock in ("collection.anki2-wal", "collection.anki2-journal"):
        if (profile / lock).exists():
            sys.exit("Anki appears to be open (lock file present). Close Anki and try again.")

    if not ffmpeg_available():
        print("WARNING: ffmpeg not found on PATH - audio will be skipped.")

    # ---- plan
    plan = []  # (old_name, new_name, kind)
    taken = set()
    for p in sorted(media.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in AUDIO_EXT and ffmpeg_available():
            if ext == audio_ext and ext != ".wav":
                continue  # already target format (re-encoding lossy->lossy is pointless)
            plan.append((p.name, unique_name(media, p.stem, audio_ext, taken), "audio"))
        elif ext in IMAGE_EXT:
            if ext == ".gif" and is_animated_gif(p):
                continue
            plan.append((p.name, unique_name(media, p.stem, ".webp", taken), "image"))

    if not plan:
        print("Nothing to convert.")
        return

    print(f"{len(plan)} files queued ({sum(1 for x in plan if x[2]=='audio')} audio, "
          f"{sum(1 for x in plan if x[2]=='image')} image)")
    if args.dry_run:
        for old, new, kind in plan:
            print(f"  [{kind}] {old}  ->  {new}")
        print("\nDry run - nothing changed.")
        return

    # ---- backups
    bak = profile / "collection.anki2.pre-compress.bak"
    shutil.copy2(coll, bak)
    print(f"Backed up collection to {bak.name}")
    orig_dir = profile / "media_originals_backup"
    if args.keep_originals:
        orig_dir.mkdir(exist_ok=True)

    # ---- convert
    tmp_dir = media / ".compress_tmp"
    tmp_dir.mkdir(exist_ok=True)
    renames = {}  # old -> new (only successful + smaller)
    before = after = 0
    for i, (old, new, kind) in enumerate(plan, 1):
        src, tmp = media / old, tmp_dir / new
        print(f"[{i}/{len(plan)}] {old}", end=" ", flush=True)
        ok = (convert_audio(src, tmp, args.audio, args.audio_bitrate) if kind == "audio"
              else convert_image(src, tmp, args.webp_quality, args.max_image_size))
        if not ok:
            print("-> FAILED, kept original")
            tmp.unlink(missing_ok=True)
            continue
        s_old, s_new = src.stat().st_size, tmp.stat().st_size
        # WAV is always worth converting; for others require a real saving
        if src.suffix.lower() != ".wav" and s_new >= s_old * 0.95:
            print(f"-> no gain ({human(s_old)} vs {human(s_new)}), kept original")
            tmp.unlink()
            continue
        shutil.move(str(tmp), str(media / new))
        if args.keep_originals:
            shutil.move(str(src), str(orig_dir / old))
        else:
            src.unlink()
        renames[old] = new
        before += s_old
        after += s_new
        print(f"-> {new}  {human(s_old)} -> {human(s_new)}")
    shutil.rmtree(tmp_dir, ignore_errors=True)

    if not renames:
        print("No files were replaced; collection untouched.")
        return

    # ---- rewrite note fields
    print("\nUpdating note references...")
    db = sqlite3.connect(coll)
    cur = db.cursor()
    now = int(time.time())
    changed = 0
    for nid, flds in cur.execute("SELECT id, flds FROM notes").fetchall():
        new_flds = flds
        for old, new in renames.items():
            if old not in new_flds:
                continue
            esc = re.escape(old)
            new_flds = re.sub(r"\[sound:" + esc + r"\]", f"[sound:{new}]", new_flds)
            new_flds = re.sub(r'(src=["\'])' + esc + r'(["\'])', r"\g<1>" + new + r"\g<2>", new_flds)
        if new_flds != flds:
            db.execute("UPDATE notes SET flds=?, mod=?, usn=-1 WHERE id=?", (new_flds, now, nid))
            changed += 1
    db.commit()
    db.close()

    print(f"\nDone.  {len(renames)} files replaced, {changed} notes updated.")
    print(f"Media size for those files: {human(before)} -> {human(after)} "
          f"(saved {human(before-after)}, {100*(1-after/max(before,1)):.0f}%)")
    print("\nNext: open Anki -> Tools -> Check Media (should report no missing files), then sync.")
    if args.audio == "opus":
        print("Note: .ogg/Opus plays on Anki desktop, AnkiDroid and recent AnkiMobile. "
              "If you use an old iOS device, re-run with --audio mp3.")


if __name__ == "__main__":
    main()
