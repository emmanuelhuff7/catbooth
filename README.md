# catbooth

Wraps a video in a pixel-art Photo Booth style window with little cats along the bottom bar. The whole frame is drawn in code with Pillow (palette, 3x5 pixel font, sprites), then ffmpeg composites your video into the hole.

![frame preview](assets/preview.png)

## Usage

```
python3 catbooth.py input.mov
python3 catbooth.py input.mov -o out.mp4
python3 catbooth.py input.mov --alpha        # ProRes 4444 .mov, transparent rounded corners
python3 catbooth.py input.mov --fit          # letterbox instead of crop-to-fill
python3 catbooth.py input.mov --frame-only   # export just the frame PNG
```

## Requirements

Python 3, Pillow, and `ffmpeg`/`ffprobe` on your PATH.

```
pip install pillow
brew install ffmpeg
```
