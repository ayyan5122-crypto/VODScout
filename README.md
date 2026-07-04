# VODScout

VODScout is a desktop application that helps gaming streamers quickly find and clip engaging moments from Twitch VODs using AI-assisted transcription and highlight detection.

---

## Features

- Download Twitch VODs
- Download only specific timestamp ranges
- Faster-Whisper transcription
- Transcript chunking
- Claude prompt generation
- Highlight review studio
- Automatic clip generation
- Export clips for YouTube Shorts, TikTok and Instagram Reels

---

## Requirements

- Python 3.11 or newer
- FFmpeg
- Git (optional, for cloning the repository)

---

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/VODScout.git
cd VODScout
```

Install the required Python packages:

```bash
py -m pip install -r requirements.txt
```

Run the application:

```bash
python app.py
```

---

## Workflow

1. Paste a Twitch VOD URL
2. Download the VOD (or a timestamp range)
3. Transcribe with Faster-Whisper
4. Generate Claude prompts
5. Analyze prompts with Claude
6. Import highlights
7. Review and edit highlights
8. Generate clips automatically
9. Upload clips to your preferred platform

---

## License

This project is for personal use.