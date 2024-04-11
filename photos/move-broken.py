import logging
from argparse import ArgumentParser
from pathlib import Path

from genutility.args import is_dir
from genutility.filesystem import scandir_ext
from genutility.rich import Progress
from PIL import Image, UnidentifiedImageError
from pillow_heif import register_avif_opener, register_heif_opener
from rich.progress import Progress as RichProgress

register_heif_opener()
register_avif_opener()


def main():
    parser = ArgumentParser()
    parser.add_argument("path", type=is_dir)
    parser.add_argument("--subdir", default="broken")
    args = parser.parse_args()

    ext = (".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".png", ".heic", ".webp", ".avif", ".jxl")

    with RichProgress() as progress:
        p = Progress(progress)
        for entry in p.track(scandir_ext(args.path, extensions=ext)):
            path = Path(entry)

            if path.parent.name == args.subdir:
                continue

            try:
                with Image.open(path, "r") as img:
                    img.load()
            except UnidentifiedImageError:
                broken = True
            except OSError as e:
                if e.args[0] == "broken data stream when reading image file":
                    broken = True
                elif e.args[0].startswith("image file is truncated"):
                    broken = True
                else:
                    raise
            else:
                broken = False

            if broken:
                out = path.parent / args.subdir / path.name
                if out.exists():
                    logging.warning("Moving %s failed because image already exists", path)
                    continue

                out.parent.mkdir(parents=True, exist_ok=True)
                p.print(f"move {path} to {out}")
                path.rename(out)


if __name__ == "__main__":
    main()
