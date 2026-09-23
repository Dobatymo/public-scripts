import logging
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path
from typing import Container, Iterator, List, Union, cast

from genutility.args import (
    between,
    is_dir,
    is_dir_nofollow,
    is_dir_or_new_nofollow,
    is_file_or_dir,
    is_file_or_new_nofollow,
    suffix,
)
from genutility.filesystem import MyDirEntry, entrysuffix, fileextensions, scandir_rec
from genutility.rich import Progress
from genutility.videofile import NoGoodFrame, grab_pic
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress

logger = logging.getLogger(__name__)


def iterfiles(inpath: Path, recursive: bool, suffixes: Container[str], follow_links: bool) -> Iterator[MyDirEntry]:
    for entry in scandir_rec(inpath, dirs=False, rec=recursive, follow_symlinks=follow_links, relative=True):
        entry = cast(MyDirEntry, entry)

        if entrysuffix(entry).lower() not in suffixes:
            logger.debug("Skipping non-video file %s", entry.path)
            continue

        yield entry


def validate_output_paths(inpath: Path, outpath: Path, pos: Union[float, List[float]], overwrite: bool) -> None:
    if isinstance(pos, float):
        outpaths = [outpath]
    else:
        outpaths = [outpath.with_suffix(f".{i}{outpath.suffix}") for i in range(len(pos))]

    for actual_outpath in outpaths:
        output_exists = actual_outpath.exists()
        is_file_or_new_nofollow(actual_outpath)
        if output_exists and actual_outpath.samefile(inpath):
            raise ArgumentTypeError(f"input and output paths refer to the same file: {inpath}")
        if output_exists and not overwrite:
            raise FileExistsError(actual_outpath)


def main():
    DEFAULT_FORMAT = ".png"

    parser = ArgumentParser()
    parser.add_argument("inpath", type=is_file_or_dir)
    parser.add_argument("outpath", nargs="?", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debugging output")
    parser.add_argument(
        "-f",
        "--format",
        type=suffix,
        choices=(".jpg", ".png", ".tiff"),
        default=DEFAULT_FORMAT,
        help="Picture format of the output file, if outpath is a directory.",
    )
    parser.add_argument("-w", "--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="Recurse into subfolders if input is a directory."
    )
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")

    parser.add_argument("--pos", nargs="+", default=[0.5], type=between(0, 1), help="List of file position ratios")
    parser.add_argument("--backend", choices=("av", "cv"), default="cv")
    args = parser.parse_args()

    try:
        input_is_file = args.inpath.is_file()
        if not input_is_file:
            (is_dir if args.follow_links or args.outpath is not None else is_dir_nofollow)(args.inpath)
        if args.outpath is not None:
            (is_file_or_new_nofollow if input_is_file else is_dir_or_new_nofollow)(args.outpath)
    except ArgumentTypeError as e:
        parser.error(str(e))

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=NullHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    pos = args.pos[0] if len(args.pos) == 1 else args.pos

    if input_is_file:
        if args.outpath is None:
            path_out = args.inpath.with_suffix(args.format)
        else:
            if args.outpath.is_dir():
                raise ValueError("outpath cannot be a directory if inpath is a file")

            path_out = args.outpath

        try:
            validate_output_paths(args.inpath, path_out, pos, args.overwrite)
        except ArgumentTypeError as e:
            parser.error(str(e))
        except FileExistsError as e:
            parser.error(f"{e} already exists; use --overwrite to replace it")

        grab_pic(args.inpath, path_out, pos, args.overwrite, args.backend)

    elif args.inpath.is_dir():
        video_suffixes = {"." + ext for ext in fileextensions.video}

        it = iterfiles(args.inpath, args.recursive, video_suffixes, args.follow_links)
        if not args.recursive:
            it = sorted(it, key=lambda entry: entry.path)

        with RichProgress() as p:
            progress = Progress(p)
            for entry in progress.track(it, description="Reading files"):
                path_in = Path(entry.path)

                if args.outpath is None:
                    path_out = path_in.with_suffix(args.format)
                else:
                    path_out = (args.outpath / entry.relpath).with_suffix(args.format)

                logger.info("Processing %s", path_in)
                try:
                    validate_output_paths(path_in, path_out, pos, args.overwrite)
                    grab_pic(path_in, path_out, pos, args.overwrite, args.backend)
                except FileExistsError:
                    logger.info("Skipping existing file %s", path_in)
                except ArgumentTypeError as e:
                    logger.warning("Skipping invalid output path %s: %s", path_out, e)
                except NoGoodFrame as e:
                    logger.warning("Could not grab frame from %s: %s", path_in, e)
    else:
        parser.error("inpath is neither file nor directory")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
