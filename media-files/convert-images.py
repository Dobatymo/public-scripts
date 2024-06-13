import logging
import os
from pathlib import Path
from shutil import copystat

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


def convert(inpath: Path, bitmap: bool = True, tiff: bool = True, remove_originals: bool = False) -> None:
    """Recursively within `inpath`, converts bitmap files to PNG
    and uncompressed tiff files to losslessly compressed ones.
    """

    for path in inpath.rglob("*"):
        if bitmap and path.suffix in {".bmp"}:
            pngfile = path.with_suffix(".png")
            if not pngfile.exists():
                try:
                    Image.open(path).save(pngfile, compress_level=9)
                    copystat(path, pngfile)
                    logger.info("Converted: %s\n-> %s", path, pngfile)

                    if remove_originals:
                        path.unlink()
                except UnidentifiedImageError as e:
                    logger.warning("%s", e)

        if tiff and path.suffix in {".tif", ".tiff"}:
            newfile = path.with_suffix(".new" + path.suffix)
            if not newfile.exists():
                im = Image.open(path)
                if im.info["compression"] in {"raw", "tiff_raw_16", "packbits", "tiff_lzw"}:  # bad lossless compression
                    im.save(str(newfile), "tiff", compression="tiff_deflate", tiffinfo=im.tag)  # might lose metadata

                    if newfile.stat().st_size >= path.stat().st_size:
                        logger.info("Tried to convert %s, but the result file was larger than the original", path)
                        newfile.unlink()
                        continue

                    copystat(path, newfile)
                    logger.info("Converted: %s\n-> %s", path, newfile)

                    if remove_originals:
                        del im
                        os.replace(newfile, path)

                elif im.info["compression"] in {"tiff_adobe_deflate", "tiff_deflate"}:  # good lossless compression
                    logger.debug("%s is already compressed", path)

                elif im.info["compression"] in {"tiff_jpeg", "jpeg"}:  # lossy compression
                    logger.debug("%s is already lossy compressed", path)

                elif im.info["compression"] in {
                    "tiff_ccitt",
                    "group3",
                    "group4",
                    "tiff_thunderscan",
                    "tiff_sgilog",
                    "tiff_sgilog24",
                    "lzma",
                    "zstd",
                    "webp",
                }:
                    raise RuntimeError(f"{path} uses an unhandled TIFF compression : {im.info}")

                else:
                    raise RuntimeError(f"{path} uses an unknown TIFF compression : {im.info}")


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import is_dir

    parser = ArgumentParser()
    parser.add_argument("path", type=is_dir, help="Path to scan for image files")
    parser.add_argument("--bitmaps", action="store_true", help="Convert bmp images")
    parser.add_argument("--tiffs", action="store_true", help="Convert tiff images")
    parser.add_argument(
        "--remove-originals", action="store_true", help="Remove the original image files after they have been converted"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Display debug messages")
    args = parser.parse_args()

    if not any([args.bitmaps, args.tiffs]):
        parser.error("Must specify at least one file format to convert")

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    convert(args.path, bitmap=args.bitmaps, tiff=args.tiffs, remove_originals=args.remove_originals)
