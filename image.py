import logging
from argparse import ArgumentParser, Namespace
from os import fspath
from pathlib import Path
from typing import Any, Dict

import pyexiv2
from genutility.args import is_file
from PIL import Image, ImageOps

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
    logging.info(f"Found meta data: {', '.join(metadata.keys())}")

    if args.grayscale:
        img = ImageOps.grayscale(img)

    if args.resize_ratio or args.resize_pixel:
        if args.resize_ratio:
            new_size = (int(img.size[0] * args.resize_ratio[0]), int(img.size[1] * args.resize_ratio[1]))
        elif args.resize_pixel:
            new_size = args.resize_pixel
        img = img.resize(new_size, resample=Image.Resampling.LANCZOS)

    logging.debug("Saving image to <%s>", args.out_path)
    img.save(args.out_path)

    logging.debug("Adding metadata to <%s>", args.out_path)
    write_meta(fspath(args.out_path), metadata)


def main():
    parser = ArgumentParser("Simple image operations")
    parser.add_argument("--in-path", type=is_file, required=True)
    parser.add_argument("--out-path", type=Path, required=True)
    parser.add_argument("--grayscale", action="store_true")
    parser.add_argument("--resize-ratio", nargs=2, metavar=("W", "H"), type=float)
    parser.add_argument("--resize-pixel", nargs=2, metavar=("W", "H"), type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if not args.overwrite:
        if args.in_path.samefile(args.out_path):
            parser.error("`--in-path` cannot be equal to `--out-path` without `--overwrite`")

    if args.resize_ratio and args.resize_pixel:
        parser.error("Cannot specify `--resize-ratio` and `--resize-pixel`")

    process(args)


if __name__ == "__main__":
    main()
