# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "boto3",
#     "genutility[args]",
#     "rich",
# ]
# ///

import argparse
import logging
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlsplit

import boto3
from genutility.args import positive_int
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn

DEFAULT_WORKERS = 8
logger = logging.getLogger(__name__)


def parse_s3_path(value: str) -> Tuple[str, str]:
    parsed = urlsplit(value)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("expected an S3 path like s3://bucket/prefix/")

    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return parsed.netloc, prefix


def filter_prefixes_without_matches(prefixes: Set[str], matches: Iterable[str]) -> List[str]:
    """Return prefixes for which no match starts with the prefix."""
    sorted_matches = sorted(matches)
    return [
        prefix
        for prefix in sorted(prefixes)
        if (index := bisect_left(sorted_matches, prefix)) == len(sorted_matches)
        or not sorted_matches[index].startswith(prefix)
    ]


def empty_directory_keys(objects: Iterable[Mapping[str, object]]) -> List[str]:
    markers = set()
    files = []

    for obj in objects:
        key = obj["Key"]
        if not isinstance(key, str):
            continue
        if key.endswith("/") and obj.get("Size") == 0:
            markers.add(key)
        else:
            files.append(key)

    return filter_prefixes_without_matches(markers, files)


def list_objects(
    client: object, bucket: str, prefix: str, progress: Optional[Any] = None, task_id: Optional[Any] = None
) -> Iterable[Mapping[str, object]]:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = page.get("Contents", ())
        if progress is not None and task_id is not None:
            progress.advance(task_id, len(objects))
        yield from objects


def delete_batch(client: object, bucket: str, keys: List[str]) -> int:
    response = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": key} for key in keys]},
    )
    errors = response.get("Errors", ())
    if errors:
        raise RuntimeError(f"S3 could not delete batch: {errors}")
    return len(keys)


def delete_keys(
    client: object,
    bucket: str,
    keys: List[str],
    workers: int = DEFAULT_WORKERS,
    progress: Optional[Any] = None,
    task_id: Optional[Any] = None,
) -> None:
    if not keys:
        return
    if workers < 1:
        raise ValueError("workers must be positive")

    batches = [keys[start : start + 1000] for start in range(0, len(keys), 1000)]
    failures = []
    with ThreadPoolExecutor(max_workers=min(workers, len(batches))) as executor:
        futures = [executor.submit(delete_batch, client, bucket, batch) for batch in batches]
        for future in as_completed(futures):
            try:
                deleted = future.result()
            except Exception as error:
                failures.append(error)
            else:
                if progress is not None and task_id is not None:
                    progress.advance(task_id, deleted)

    if failures:
        raise RuntimeError(f"{len(failures)} S3 delete batch(es) failed") from failures[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove empty S3 directory-marker objects. Any zero-byte object whose key ends in '/' "
            "is considered a directory marker."
        )
    )
    parser.add_argument("s3_path", help="S3 path to scan, for example s3://bucket/prefix/")
    parser.add_argument("--apply", action="store_true", help="Delete the empty directory markers.")
    parser.add_argument("--yes", action="store_true", help="Skip the deletion confirmation; requires --apply.")
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=DEFAULT_WORKERS,
        help=f"Parallel delete workers (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--allow-bucket-root",
        action="store_true",
        help="Allow --apply to scan and delete markers at the bucket root.",
    )
    return parser


def configure_logging(console: Console) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.yes and not args.apply:
        parser.error("--yes requires --apply")

    try:
        bucket, prefix = parse_s3_path(args.s3_path)
    except ValueError as error:
        parser.error(str(error))

    client = boto3.client("s3")
    console = Console()
    configure_logging(console)
    logger.info("Scanning %s", args.s3_path)
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        scan_task = progress.add_task("Scanning S3 objects", total=None)
        keys = empty_directory_keys(list_objects(client, bucket, prefix, progress, scan_task))
        progress.update(scan_task, total=progress.tasks[scan_task].completed)

    if not args.apply:
        console.print(f"[yellow]Dry run:[/yellow] found {len(keys)} empty directory marker(s).")
    elif not keys:
        console.print("No empty directory markers found.")
    else:
        if not args.allow_bucket_root and not prefix:
            parser.error("refusing to apply to the bucket root; use --allow-bucket-root")
        if not args.yes:
            answer = console.input(
                f"Delete {len(keys)} empty directory marker(s) below s3://{bucket}/{prefix}? "
                "Type 'delete' to continue: "
            )
            if answer.strip().lower() != "delete":
                console.print("Deletion cancelled.")
                return 0

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            delete_task = progress.add_task("Deleting directory markers", total=len(keys))
            delete_keys(client, bucket, keys, args.workers, progress, delete_task)

    label = "removed" if args.apply else "would remove"
    for key in keys:
        console.print(f"{label}: s3://{bucket}/{key}")
    console.print(f"{label} {len(keys)} empty director{'y' if len(keys) == 1 else 'ies'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
