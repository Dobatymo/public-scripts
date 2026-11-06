import argparse
import os
import sys

from genutility.args import is_file, is_file_or_new_nofollow
from genutility.file import copen, copyfilelike

parser = argparse.ArgumentParser()

parser.add_argument("-c", "--stdout", action="store_true", help="Compress or decompress to standard output.")

mode = parser.add_mutually_exclusive_group()
mode.add_argument("-d", "--decompress", action="store_true", help="Force decompression.")
mode.add_argument(
    "-z",
    "--compress",
    action="store_true",
    help="The complement to -d: forces compression, regardless of the invocation name.",
)
mode.add_argument(
    "-t",
    "--test",
    action="store_true",
    help="Check integrity of the specified file(s), but don't decompress them. This really performs a trial decompression and throws away the result.",
)

parser.add_argument("-f", "--force", action="store_true")
parser.add_argument("-k", "--keep", action="store_true")

compression = parser.add_mutually_exclusive_group()
compression.add_argument("-1", "--fast", action="store_true")
compression.add_argument("-2", action="store_true")
compression.add_argument("-3", action="store_true")
compression.add_argument("-4", action="store_true")
compression.add_argument("-5", action="store_true")
compression.add_argument("-6", action="store_true")
compression.add_argument("-7", action="store_true")
compression.add_argument("-8", action="store_true")
compression.add_argument("-9", "--best", action="store_true", default=True)

parser.add_argument("path", type=is_file)
args = parser.parse_args()

input_path = args.path
decompress = args.decompress or args.test

if decompress:
    suffix = input_path.suffix.lower()
    if suffix in (".bz2", ".bz"):
        outpath = input_path.with_suffix("")
    elif suffix in (".tbz2", ".tbz"):
        outpath = input_path.with_suffix(".tar")
    else:
        outpath = input_path.with_name(input_path.name + ".out")
else:
    outpath = input_path.with_name(input_path.name + ".bz2")

if not args.stdout and not args.test:
    try:
        output_exists = outpath.exists()
        is_file_or_new_nofollow(outpath)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    if output_exists and outpath.samefile(input_path):
        parser.error("input and output paths refer to the same file")
    if output_exists and not args.force:
        parser.error(f"{outpath} already exists; use --force to replace it")

compression_level = next(
    (level for level in range(1, 9) if getattr(args, "fast" if level == 1 else str(level))), 9
)

with input_path.open("rb") as raw:
    with copen(raw, "rb", ext=".bz2" if decompress else None) as fr:
        if args.test:
            with copen(os.devnull, "wb") as fw:
                copyfilelike(fr, fw)
        elif args.stdout:
            with copen(
                sys.stdout.buffer, "wb", ext=None if decompress else ".bz2", compresslevel=compression_level
            ) as fw:
                copyfilelike(fr, fw)
        else:
            with copen(
                outpath,
                "wb" if args.force else "xb",
                compresslevel=compression_level,
                handle_archives=not decompress,
            ) as fw:
                copyfilelike(fr, fw)

if not args.keep and not args.stdout and not args.test:
    input_path.unlink()
