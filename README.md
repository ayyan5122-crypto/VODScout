# VODScout

VODScout is a desktop application that helps creators find, review, and clip the best moments from Twitch VODs, YouTube videos, and local video files using AI-assisted transcription and highlight detection.

---

## Features

- Download Twitch VODs
- Download timestamp ranges from Twitch VODs
- Download YouTube videos
- Import local video files
- Faster-Whisper transcription
- AI-ready prompt generation
- Highlight Review Studio
- Automatic clip generation
- Export clips for YouTube Shorts, TikTok, and Instagram Reels

---

## Requirements

- Python 3.11+
- FFmpeg
- TwitchDownloaderCLI (included in the repository)
- Windows

---

## Installation

Clone the repository:

```bash
git clone https://github.com/ayyan5122-crypto/VODScout.git
cd VODScout
```

Install dependencies:

```bash
py -m pip install -r requirements.txt
```

Run the application:

```bash
python app.py
```

---

## Building the executable

Build using PyInstaller:

```bash
python -m PyInstaller --clean VODScout.spec
```

The executable will be created in:

```
dist/
```

---

## Workflow

1. Select a Twitch URL, YouTube URL, or local video.
2. (Optional) Specify a timestamp range.
3. Download or import the video.
4. Generate a transcript using Faster-Whisper.
5. Create AI prompts from transcript chunks.
6. Review detected highlights.
7. Generate clips.
8. Export clips for your preferred platform.

---

## Repository Structure

```
assets/
tools/
prompts/

app.py
downloader.py
gui.py
transcriber.py
clipper.py
chunker.py
highlight_studio.py
settings.py
```

---

## Notes

- Twitch timestamp downloads use TwitchDownloaderCLI.
- YouTube downloads use yt-dlp.
- Local videos are processed directly without downloading.

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.