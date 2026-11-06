import errno
import logging
import os.path
import shutil
import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, Namespace
from pathlib import Path

import pandas as pd
from genutility.args import empty_dir_nofollow, is_dir_or_new_nofollow, is_file_or_new_nofollow
from genutility.filesystem import is_writeable, make_readonly, make_writeable
from genutility.rich import MarkdownHighlighter, Progress, get_double_format_columns
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress
from send2trash import send2trash

logger = logging.getLogger(__name__)


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, header=0, keep_default_na=False)


def move_no_replace(src: Path, dst: Path) -> None:
    if os.name == "nt":
        try:
            src.rename(dst)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
        else:
            return
    else:
        try:
            os.link(src, dst, follow_symlinks=False)
        except FileExistsError:
            raise
        except OSError:
            pass  # Filesystems without hard links use an exclusive copy.
        else:
            src.unlink()
            return

    if src.is_symlink():
        dst.symlink_to(os.readlink(src), target_is_directory=src.is_dir())
    else:
        fw = dst.open("xb")
        try:
            with fw, src.open("rb") as fr:
                shutil.copyfileobj(fr, fw)
            shutil.copystat(src, dst)
        except BaseException:
            dst.unlink()
            raise
    src.unlink()


def move(args: Namespace, progress: Progress) -> int:
    df = read_csv(args.csv)

    assert df["path"].is_unique

    is_dir_or_new_nofollow(args.out_path)
    output_exists = args.out_path.exists()
    if output_exists and not args.ignore_non_empty_out_path:
        empty_dir_nofollow(args.out_path)

    filename_only = args.filename_only

    if filename_only:
        basepath = None
    elif args.basepath:
        basepath = args.basepath
    else:
        basepath = Path(os.path.commonpath(df["path"].tolist()))
        logger.info("Using base path `%s`", basepath)

    if args.no_check_size:
        rows = ((path, None) for path in df["path"])
    else:
        rows = df[["path", "filesize"]].itertuples(index=False)

    file_moves = []
    source_paths = set()
    source_ids = set()

    for path, filesize in rows:
        srcpath = Path(path)
        source_paths.add(srcpath.resolve())

        try:
            stats = srcpath.stat()
        except FileNotFoundError:
            logger.warning("File not found: %s", srcpath)
            continue

        if stats.st_ino:
            source_ids.add((stats.st_dev, stats.st_ino))
        actualsize = stats.st_size
        if filesize is not None and actualsize != filesize:
            logger.warning(
                "Skipping %s since actual filesize %d doesn't match expected one %d", srcpath, actualsize, filesize
            )
            continue

        if filename_only:
            destpath = args.out_path / srcpath.name
        else:
            assert basepath is not None
            destpath = args.out_path / srcpath.relative_to(basepath)

        file_moves.append((srcpath, destpath, stats))

    destinations = set()
    collision_destinations = set()
    for srcpath, destpath, _ in file_moves:
        try:
            dest_stats = destpath.stat()
        except FileNotFoundError:
            destination_id = None
        else:
            destination_id = (dest_stats.st_dev, dest_stats.st_ino)

        if destpath.resolve() in source_paths or destination_id in source_ids:
            raise FileExistsError(f"Source and destination overlap ({srcpath} -> {destpath})")

        if destpath in destinations or os.path.lexists(destpath):
            if args.collision_mode == "fail":
                raise FileExistsError(f"Destination collision: {srcpath} -> {destpath}")
            collision_destinations.add(destpath)
        destinations.add(destpath)

    if not output_exists:
        args.out_path.mkdir(parents=True)

    for srcpath, destpath, stats in file_moves:
        has_collision = destpath in collision_destinations or os.path.lexists(destpath)
        if has_collision and args.collision_mode == "fail":
            raise FileExistsError(f"Destination collision: {srcpath} -> {destpath}")
        if has_collision and args.collision_mode == "no-move":
            logger.info("Skipping `%s` because destination exists: `%s`", srcpath, destpath)
            continue
        if has_collision and destpath.is_dir():
            raise IsADirectoryError(f"Destination is a directory: {destpath}")

        if args.do:
            writeable = is_writeable(stats)
            if not writeable:
                make_writeable(srcpath, stats)

            try:
                destpath.parent.mkdir(parents=True, exist_ok=True)
                if args.collision_mode == "overwrite":
                    shutil.move(srcpath, destpath)
                else:
                    try:
                        move_no_replace(srcpath, destpath)
                    except FileExistsError:
                        if args.collision_mode == "fail":
                            raise
                        logger.info("Skipping `%s` because destination exists: `%s`", srcpath, destpath)
                        continue
            finally:
                if not writeable:
                    make_readonly(srcpath if srcpath.exists() else destpath, stats)
        else:
            action = "Overwriting" if has_collision else "Moving"
            logger.info("%s `%s` to `%s`", action, srcpath, destpath)

    return 0


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

    parser.add_argument("--log", type=is_file_or_new_nofollow, help="Write log to file")
    parser.add_argument("--do", action="store_true", help="Actually perform the operation, otherwise it's a dry run")
    parser.add_argument("--verbose", action="store_true", help="Print more information")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparser_a = subparsers.add_parser("move", formatter_class=ArgumentDefaultsHelpFormatter, help="Move files")
    subparser_a.set_defaults(func=move)
    subparser_a.add_argument("--out-path", type=is_dir_or_new_nofollow, required=True, help="Output directory")
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
    subparser_a.add_argument(
        "--collision-mode",
        choices=("fail", "overwrite", "no-move"),
        default="fail",
        metavar="MODE",
        help="Handle destination collisions: fail (default), overwrite, or no-move. Overlap with any source is an error.",
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
