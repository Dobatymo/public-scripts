import logging
from typing import Any, Callable, List, Tuple

from genutility.exceptions import MalformedFile
from genutility.file import PathType
from genutility.fileformats.srt import DEFAULT_ENCODING, SRTFile, Subtitle
from genutility.func import identity

logger = logging.getLogger(__name__)


def sync_srt(
    infile: PathType,
    outfile: PathType,
    encoding: str,
    modify_times: Callable[..., float] = identity,
    arg_times: Tuple[Any, ...] = (),
    modify_text: Callable[..., List[str]] = identity,
    arg_text: Tuple[Any, ...] = (),
) -> None:
    def sync(subtitle: Subtitle) -> None:
        subtitle.start, subtitle.end = modify_times(subtitle.start, *arg_times), modify_times(subtitle.end, *arg_times)
        subtitle.lines = modify_text(subtitle.lines, *arg_text)

    with SRTFile(infile, "r", encoding) as source, SRTFile(outfile, "w", encoding) as destination:
        for subtitle in source:
            sync(subtitle)
            destination.write_subtitle(subtitle)


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import (
        encoding_name,
        finite_float,
        is_file_or_dir,
        new_path,
        positive_float,
    )
    from genutility.filesystem import entrysuffix, scandir_rec

    parser = ArgumentParser(description="Apply simple transformations to srt subtitles files.")
    parser.add_argument("inpath", type=is_file_or_dir, help="Input file or directory.")
    parser.add_argument("outpath", type=new_path, help="Output file or directory.")
    parser.add_argument("--encoding", type=encoding_name, default=DEFAULT_ENCODING, help="File encoding")
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--fps",
        metavar=("FROM-FPS", "TO-FPS"),
        nargs=2,
        type=positive_float,
        help="Change the rate from x fps to y fps",
    )
    group.add_argument("--delay", type=finite_float, help="Delay by +x or -x seconds")

    args = parser.parse_args()

    input_is_file = args.inpath.is_file()

    if args.fps:

        def func(x: float) -> float:
            return x * args.fps[0] / args.fps[1]

    elif args.delay is not None:

        def func(x: float) -> float:
            return x + args.delay

    if not input_is_file:
        args.outpath.mkdir(parents=True)
        for infile in scandir_rec(args.inpath, dirs=False, follow_symlinks=args.follow_links, relative=True):
            if entrysuffix(infile).lower() != ".srt":
                continue
            outfile = args.outpath / infile.relpath
            outfile.parent.mkdir(parents=True, exist_ok=True)
            print(f"In file: {infile.path}")
            print(f"Out file: {outfile}")
            try:
                sync_srt(infile.path, outfile, args.encoding, func)
            except MalformedFile as e:
                logger.error("File is malformed: %s", e)
    else:
        print(f"In file: {args.inpath}")
        print(f"Out file: {args.outpath}")
        sync_srt(args.inpath, args.outpath, args.encoding, func)
