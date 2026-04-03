# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "genutility[rich]",
#     "rich",
# ]
# ///

import os
import shutil
import tempfile
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from pathlib import Path
from typing import Iterator, Optional

from genutility.rich import Progress
from rich.progress import Progress as RichProgress


def replace_symlink_with_file(link: Path, outpath: Path, *, dry_run: bool = False):
    """
    Replace a file symlink with a real file containing the target's contents.

    Returns True on success, False if skipped or failed (after logging).
    """


def replace_symlinks(
    root: Path, outdir: Optional[Path], skip_existing: bool, dry_run: bool, progress: Progress
) -> Iterator[Path]:
    """If outdir is None, replace the symlinks with real files in place."""

    if outdir is not None:
        if outdir.exists() and not outdir.is_dir():
            raise NotADirectoryError("outdir not a directory")

    with progress.task() as task:
        for link in root.rglob("*"):
            if not link.is_symlink():
                continue

            task.update(description=link.name)

            target = link.resolve(strict=True)

            if not target.is_file():
                raise ValueError(f"[SKIP] Symlink not pointing to a regular file: {link} -> {target}")

            if outdir is None:
                if dry_run:
                    print(f"[DRY-RUN] Would replace symlink {link} -> {target}")
                    continue

                fd, tmp_path_str = tempfile.mkstemp(
                    prefix=f"{link.name}.",
                    dir=str(link.parent),
                )

                tmp_path = Path(tmp_path_str)

                with target.open("rb") as src_fp, os.fdopen(fd, "wb") as tmp_fp:
                    shutil.copyfileobj(src_fp, tmp_fp)

                shutil.copystat(target, tmp_path)
                link.unlink()
                tmp_path.replace(link)

            else:
                relpath = link.relative_to(root)
                dst = outdir / relpath
                if dst.exists():
                    if skip_existing:
                        if dry_run:
                            print(f"[DRY-RUN] Skipping {link} -> {dst}")
                        continue
                    else:
                        raise FileExistsError(f"{dst} already exists")

                if dry_run:
                    print(f"[DRY-RUN] Would copy symlink {link} -> {dst}")
                    continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                # don't use dst.parent for copy, as this would use the target filename, not the link filename
                # also don't use copyfile/copystat since copy2 has some windows specific optimizations, eg. it keeps ADS
                shutil.copy2(target, dst, follow_symlinks=True)

            task.advance(delta=1)


def main() -> int:
    parser = ArgumentParser(
        description="Replace file symlinks with real files.", formatter_class=ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Paths to scan for symlinks.",
    )
    parser.add_argument(
        "--do",
        action="store_true",
        help="Actually do the copy and/or replacements. Otherwise just show what would be done.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--inplace",
        action="store_true",
        help="Replace the symlinks with actual files",
    )
    group.add_argument(
        "--out-path",
        type=Path,
        help="Place the files the symlinks point to in this directory",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="When copying to --out-path, skip existing files",
    )
    args = parser.parse_args()

    if args.skip_existing and args.out_path is None:
        parser.error("--skip-existing can only be used with --out-path")

    dry_run = not args.do
    with RichProgress() as p:
        progress = Progress(p)
        for root in args.paths:
            replace_symlinks(root, args.out_path, args.skip_existing, dry_run, progress)

    return 0


if __name__ == "__main__":
    main()
