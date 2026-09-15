# anki-media-compressor

anki_compress_media.py — shrink an Anki profile's media folder.

- audio  : wav / flac / aiff / m4a / mp3 / ogg  ->  Opus (.ogg)  \[or MP3 with --audio mp3\]

- images : png / jpg / jpeg / bmp / tiff / static gif  ->  WebP

Then rewrites every \[sound:...\] and \<img src="..."\> reference in your notes
so nothing breaks. Animated GIFs, SVGs and files that would not get smaller
are left untouched.

REQUIREMENTS

- pip install pillow
- ffmpeg on PATH  (<https://ffmpeg.org>)   — used for audio

## USAGE

1. CLOSE ANKI.  (The script refuses to run otherwise.)
2. `python anki_compress_media.py "/path/to/Anki2/User 1" --dry-run   # preview`
3. `python anki_compress_media.py "/path/to/Anki2/User 1"             # do it`
4. Open Anki -> Tools -> Check Media, then sync.

Profile folder locations:

- Windows : `%APPDATA%\\Anki2\\\<profile\>`
- macOS   : `~/Library/Application Support/Anki2/\<profile\>`
- Linux   : `~/.local/share/Anki2/\<profile\>`

Options:

- `--audio opus|mp3`      opus = smallest (default). Use mp3 if you rely on
                        older devices / iOS versions that can't play .ogg.
- `--audio-bitrate 48k`   opus: 32k-64k is plenty for speech. mp3: use 96k+.
- `--webp-quality 80`     0-100, lossy WebP quality.
- `--max-image-size 1600` downscale longest side to this many px (0 = keep).
- `--keep-originals`      move originals to `\<profile\>/media_originals_backup`
                        instead of deleting them.
- `--jobs N`              parallel conversions (default: CPU count).
- `--dry-run`             only report what would happen.

A copy of collection.anki2 is written to `\<profile\>/collection.anki2.pre-compress.bak`
before anything is changed.
