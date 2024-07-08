import logging
import os.path
import shutil
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from pathlib import Path

import pandas as pd
from genutility.filesystem import is_writeable, make_readonly, make_writeable
from genutility.rich import MarkdownHighlighter, Progress, get_double_format_columns
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress
from send2trash import send2trash

logger = logging.getLogger(__name__)


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, header=0, keep_default_na=False)


def move(args: Namespace, progress: Progress) -> int:
    df = read_csv(args.csv)

    assert df["path"].is_unique
    assert not args.no_check_size, "Unsupported"

    if args.out_path.exists():
        if not args.ignore_non_empty_out_path:
            if list(args.out_path.iterdir()):
                raise ValueError("Output path is not a empty directory")
    else:
        args.out_path.mkdir(parents=True)

    if args.basepath:
        basepath = args.basepath
    else:
        basepath = Path(os.path.commonpath(df["path"].tolist()))
        logger.info("Using base path `%s`", basepath)

    for path, filesize in df[["path", "filesize"]].itertuples(index=False):
        srcpath = Path(path)

        try:
            stats = srcpath.stat()
        except FileNotFoundError:
            logger.warning("File not found: %s", srcpath)
            continue

        actualsize = stats.st_size
        if actualsize != filesize:
            logger.warning(
                "Skipping %s since actual filesize %d doesn't match expected one %d", srcpath, actualsize, filesize
            )
            continue

        relpath = srcpath.relative_to(basepath)
        destpath = args.out_path / relpath
        if args.do:
            writeable = is_writeable(stats)
            if not writeable:
                make_writeable(srcpath, stats)

            destpath.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(srcpath, destpath)

            if not writeable:
                make_readonly(destpath, stats)
        else:
            logger.info("Moving `%s` to `%s`", srcpath, destpath)


def trash(args: Namespace, progress: Progress) -> int:
    df = read_csv(args.csv)

    assert df["path"].is_unique

    if args.no_check_size:
        for path in df["path"]:
            if args.do:
                send2trash(path)
            else:
                logger.info("Moving `%s` to trash", path)
    else:
        for path, filesize in df[["path", "filesize"]].itertuples(index=False):
            actualsize = os.path.getsize(path)
            if actualsize != filesize:
                logger.warning(
                    "Skipping %s since actual filesize %d doesn't match expected one %d", path, actualsize, filesize
                )
                continue

            if args.do:
                send2trash(path)
            else:
                logger.info("Moving `%s` to trash", path)
    return 0


if __name__ == "__main__":
    from genutility.args import is_file

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--csv",
        type=is_file,
        help="Read files from csv file. The file is expected to contain a header with keys like path, filesize, or mtime.",
    )

    parser.add_argument("--log", type=Path, help="Write log to file")
    parser.add_argument("--do", action="store_true", help="Actually perform the operation, otherwise it's a dry run")
    parser.add_argument("--verbose", action="store_true", help="Print more information")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparser_a = subparsers.add_parser("move", formatter_class=ArgumentDefaultsHelpFormatter, help="Move files")
    subparser_a.set_defaults(func=move)
    subparser_a.add_argument("--out-path", type=Path, required=True, help="Output directory")
    subparser_a.add_argument(
        "--filename-only",
        action="store_true",
        help="Don't keep directory structure, but move file directly to output directory",
    )
    subparser_a.add_argument(
        "--no-check-size", action="store_true", help="Don't compare file sizes given in input file with actual sizes"
    )
    subparser_a.add_argument(
        "--basepath",
        type=Path,
        help="Use basepath to create relative paths from absolute ones. If not specified the longest common path will be used",
    )
    subparser_a.add_argument(
        "--ignore-non-empty-out-path",
        action="store_true",
        help="Use outpath even if it's not empty. Existing files may be overwritten.",
    )

    subparser_b = subparsers.add_parser(
        "trash", formatter_class=ArgumentDefaultsHelpFormatter, help="Send all files to trash"
    )
    subparser_b.set_defaults(func=trash)
    subparser_b.add_argument(
        "--no-check-size", action="store_true", help="Don't compare file sizes given in input file with actual sizes"
    )

    args = parser.parse_args()

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=MarkdownHighlighter())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.log:
        handler = logging.FileHandler(args.log, encoding="utf-8", delay=True)
        logger.addHandler(handler)

    with RichProgress(*get_double_format_columns()) as progress:
        p = Progress(progress)
        try:
            ret = args.func(args, p)
        except FileExistsError as e:
            ret = 2
            logger.error(str(e))
        except Exception:
            ret = 2
            logger.exception("Action failed")

    sys.exit(ret)
