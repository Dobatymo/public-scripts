# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility>=0.0.123",
#     "pillow",
#     "pyexiv2",
# ]
# ///
import logging
from argparse import ArgumentParser, Namespace
from os import fspath
from typing import Any, Dict

import pyexiv2
from genutility.args import is_file, is_file_or_new_nofollow, positive_float, positive_int
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

metakeys = ["exif", "iptc", "xmp", "raw_xmp", "comment", "icc"]


def read_meta(path: str) -> Dict[str, Any]:
    with pyexiv2.Image(path) as metafile:
        metadata = {k: getattr(metafile, f"read_{k}")() for k in metakeys}

    return {k: v for k, v in metadata.items() if v}


def write_meta(path: str, metadata: Dict[str, Any]) -> None:
    if metadata:
        with pyexiv2.Image(path) as metafile:
            for k, v in metadata.items():
                modify_func = getattr(metafile, f"modify_{k}")
                modify_func(v)


def process(args: Namespace) -> None:
    img = Image.open(args.in_path)
    metadata = read_meta(fspath(args.in_path))
    logger.info("Found meta data: %s", ", ".join(metadata))

    if args.grayscale:
        img = ImageOps.grayscale(img)

    if args.resize_ratio or args.resize_pixel:
        if args.resize_ratio:
            new_size = (int(img.size[0] * args.resize_ratio[0]), int(img.size[1] * args.resize_ratio[1]))
        elif args.resize_pixel:
            new_size = args.resize_pixel
        img = img.resize(new_size, resample=Image.Resampling.LANCZOS)

    logger.debug("Saving image to <%s>", args.out_path)
    img.save(args.out_path)

    logger.debug("Adding metadata to <%s>", args.out_path)
    write_meta(fspath(args.out_path), metadata)


def main():
    parser = ArgumentParser("Simple image operations")
    parser.add_argument("--in-path", type=is_file, required=True)
    parser.add_argument("--out-path", type=is_file_or_new_nofollow, required=True)
    parser.add_argument("--grayscale", action="store_true")
    parser.add_argument("--resize-ratio", nargs=2, metavar=("W", "H"), type=positive_float)
    parser.add_argument("--resize-pixel", nargs=2, metavar=("W", "H"), type=positive_int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.out_path.exists() and args.out_path.samefile(args.in_path):
        parser.error("`--in-path` and `--out-path` refer to the same file")

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if not args.overwrite and args.out_path.exists():
        parser.error("`--out-path` already exists; use `--overwrite` to replace it")

    if args.resize_ratio and args.resize_pixel:
        parser.error("Cannot specify `--resize-ratio` and `--resize-pixel`")

    process(args)


if __name__ == "__main__":
    main()
