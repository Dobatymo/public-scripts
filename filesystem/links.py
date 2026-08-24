# Python 3.8 is required for pathlib.Path.unlink(missing_ok=True).
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,rich]>=0.0.122",
#     "rich",
# ]
# ///

import logging
import os
import secrets
import shutil
import tempfile
from argparse import (
    ArgumentDefaultsHelpFormatter,
    ArgumentParser,
    Namespace,
    RawDescriptionHelpFormatter,
)
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

from genutility.args import positive_int
from genutility.rich import Progress
from rich.progress import Progress as RichProgress

MAPPING_MODES = ("root", "basename", "tail", "link-tree", "unique-search")
TARGET_STYLES = ("absolute", "relative", "preserve")
MATERIALIZATION_METHODS = ("copy", "hardlink")
logger = logging.getLogger(__name__)


class RepairPlan(NamedTuple):
    link: Path
    old_target_text: Path
    old_target: Path
    candidate: Path
    new_target_text: Path


REPAIR_EPILOG = r"""
Mapping modes
-------------
root
  Replace an exact old target root and preserve the path below it.
  links.py repair C:\links Y:\new --mapping root --old-root X:\old

basename
  Look directly under NEW_ROOT using only the old target's filename.
  links.py repair C:\links Y:\flat --mapping basename

tail
  Retain a fixed number of components from the end of the old target.
  links.py repair C:\links Y:\new --mapping tail --keep-parts 3

link-tree
  Mirror each link's path below SCAN_ROOT under NEW_ROOT.
  links.py repair C:\links Y:\new --mapping link-tree --recursive

unique-search
  Search NEW_ROOT recursively by filename and require exactly one match.
  links.py repair C:\links Y:\new --mapping unique-search --recursive

Target styles
-------------
absolute stores the full candidate path. relative stores a path from the
link's parent. preserve keeps each original link's absolute/relative style.

Repair scans immediate broken links and dry-runs by default. Use --recursive,
--include-valid, and --apply to expand or execute the plan. All temporary links
are validated before any original link is changed.
"""


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


def old_target(link: Path) -> Tuple[Path, Path]:
    target_text = Path(os.readlink(str(link)))
    target_string = str(target_text)

    if os.name == "nt":
        if target_string.startswith("\\\\?\\UNC\\"):
            target_string = "\\\\" + target_string[8:]
        elif target_string.startswith("\\\\?\\"):
            target_string = target_string[4:]
        target_text = Path(target_string)

    if target_text.is_absolute():
        return target_text, absolute_path(target_text)

    return target_text, absolute_path(link.parent / target_text)


def is_plain_directory(entry: os.DirEntry) -> bool:
    if not entry.is_dir(follow_symlinks=False):
        return False
    stats = entry.stat(follow_symlinks=False)
    return not getattr(stats, "st_file_attributes", 0) & 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def iter_symlinks(root: Path, recursive: bool) -> Iterator[Path]:
    with os.scandir(str(root)) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                yield path
            elif recursive and is_plain_directory(entry):
                yield from iter_symlinks(path, recursive=True)


def iter_named_paths(root: Path, name: str) -> Iterator[Path]:
    # ponytail: search once per link; build a filename index if large trees make this measurably slow.
    with os.scandir(str(root)) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.name == name and path.exists():
                yield path
            if is_plain_directory(entry):
                yield from iter_named_paths(path, name)


def map_candidate(
    mapping: str,
    link: Path,
    target: Path,
    scan_root: Path,
    new_root: Path,
    old_root: Optional[Path],
    keep_parts: Optional[int],
) -> Path:
    if mapping == "root":
        if old_root is None:
            raise ValueError("--mapping root requires --old-root")
        try:
            relative = target.relative_to(old_root)
        except ValueError as e:
            raise ValueError(f"old target is outside --old-root {old_root}: {target}") from e
        candidate = new_root / relative

    elif mapping == "basename":
        if not target.name:
            raise ValueError(f"old target has no filename: {target}")
        candidate = new_root / target.name

    elif mapping == "tail":
        if keep_parts is None:
            raise ValueError("--mapping tail requires --keep-parts")
        parts = target.parts[1:] if target.anchor else target.parts
        if keep_parts > len(parts):
            raise ValueError(f"--keep-parts {keep_parts} exceeds the {len(parts)} available target components")
        candidate = new_root.joinpath(*parts[-keep_parts:])

    elif mapping == "link-tree":
        candidate = new_root / link.relative_to(scan_root)

    elif mapping == "unique-search":
        if not target.name:
            raise ValueError(f"old target has no filename: {target}")
        matches = []
        for path in iter_named_paths(new_root, target.name):
            if path != link:
                matches.append(path)
                if len(matches) == 2:
                    break
        if not matches:
            raise ValueError(f"no match for {target.name!r} below {new_root}")
        if len(matches) > 1:
            joined = ", ".join(str(path) for path in matches)
            raise ValueError(f"multiple matches for {target.name!r}, including: {joined}")
        candidate = matches[0]

    else:
        raise ValueError(f"unknown mapping: {mapping}")

    candidate = absolute_path(candidate)
    if candidate == link:
        raise ValueError("candidate is the link itself")
    if not candidate.exists():
        raise ValueError(f"candidate does not exist: {candidate}")
    return candidate


def format_target(link: Path, candidate: Path, target_text: Path, style: str) -> Path:
    if style == "preserve":
        style = "absolute" if target_text.is_absolute() else "relative"

    if style == "absolute":
        return candidate
    if style == "relative":
        try:
            return Path(os.path.relpath(str(candidate), start=str(link.parent)))
        except ValueError as e:
            raise ValueError(f"cannot make target relative to {link.parent}: {candidate}") from e
    raise ValueError(f"unknown target style: {style}")


def create_temporary_link(plan: RepairPlan) -> Path:
    for _ in range(10):
        temp_link = plan.link.with_name(f".repair-{secrets.token_hex(4)}.link")
        try:
            temp_link.symlink_to(plan.new_target_text, target_is_directory=plan.candidate.is_dir())
        except FileExistsError:
            continue

        try:
            validate_link(temp_link, plan.candidate, "temporary")
        except BaseException:
            temp_link.unlink(missing_ok=True)
            raise
        return temp_link

    raise FileExistsError(f"could not reserve a temporary link beside {plan.link}")


def validate_link(link: Path, candidate: Path, label: str) -> None:
    if not link.is_symlink() or not link.exists():
        raise OSError(f"{label} link is invalid: {link}")
    if not os.path.samefile(str(link), str(candidate)):
        raise OSError(f"{label} link resolves to the wrong target: {link}")


def validate_unchanged(plan: RepairPlan, temp_link: Path) -> None:
    current_target_text, current_target = old_target(plan.link)
    if current_target_text != plan.old_target_text or current_target != plan.old_target:
        raise OSError(f"link changed after it was planned: {plan.link}")
    validate_link(temp_link, plan.candidate, "staged")


def replace_staged_link(plan: RepairPlan, temp_link: Path) -> None:
    validate_unchanged(plan, temp_link)

    if os.name != "nt" or not plan.candidate.is_dir():
        os.replace(str(temp_link), str(plan.link))
        return

    for _ in range(10):
        backup = plan.link.with_name(f".repair-backup-{secrets.token_hex(4)}.link")
        if not os.path.lexists(str(backup)):
            break
    else:
        raise FileExistsError(f"could not reserve a backup path beside {plan.link}")

    try:
        os.replace(str(plan.link), str(backup))
        os.replace(str(temp_link), str(plan.link))
        validate_link(plan.link, plan.candidate, "replacement")
    except BaseException:
        if os.path.lexists(str(backup)):
            if os.path.lexists(str(plan.link)) and os.path.lexists(str(temp_link)):
                logger.critical(
                    "Could not restore %s because another path appeared there; its original link remains at %s",
                    plan.link,
                    backup,
                )
            else:
                plan.link.unlink(missing_ok=True)
                try:
                    os.replace(str(backup), str(plan.link))
                except OSError as rollback_error:
                    logger.critical(
                        "Could not restore %s; its original link remains at %s: %s",
                        plan.link,
                        backup,
                        rollback_error,
                    )
        raise
    else:
        try:
            backup.unlink()
        except OSError as e:
            logger.warning("Repaired %s, but could not remove its backup %s: %s", plan.link, backup, e)


def apply_repair_plans(plans: List[RepairPlan]) -> Tuple[int, int]:
    staged: List[Tuple[RepairPlan, Path]] = []
    repaired = 0
    failed = 0

    try:
        try:
            for plan in plans:
                staged.append((plan, create_temporary_link(plan)))
        except OSError as e:
            logger.error("Could not stage replacements; no links were changed: %s", e)
            return 0, 1

        for plan, temp_link in staged:
            try:
                replace_staged_link(plan, temp_link)
            except OSError as e:
                logger.error("Stopped after %d replacements while replacing %s: %s", repaired, plan.link, e)
                failed += 1
                break
            else:
                repaired += 1
                logger.info("Repaired %s: %s -> %s", plan.link, plan.old_target_text, plan.new_target_text)
    finally:
        for _, temp_link in staged:
            temp_link.unlink(missing_ok=True)

    return repaired, failed


def repair_links(
    basepath: Path,
    newbasepath: Path,
    *,
    mapping: str,
    old_root: Optional[Path] = None,
    keep_parts: Optional[int] = None,
    target_style: str = "preserve",
    recursive: bool = False,
    include_valid: bool = False,
    apply: bool = False,
) -> Dict[str, int]:
    scan_root = absolute_path(basepath)
    new_root = absolute_path(newbasepath)
    normalized_old_root = absolute_path(old_root) if old_root is not None else None

    if not scan_root.is_dir():
        raise NotADirectoryError(f"scan root is not a directory: {scan_root}")
    if not new_root.is_dir():
        raise NotADirectoryError(f"new root is not a directory: {new_root}")

    plans: List[RepairPlan] = []
    stats = {"planned": 0, "repaired": 0, "skipped": 0, "failed": 0}

    for link in iter_symlinks(scan_root, recursive):
        if not include_valid and link.exists():
            stats["skipped"] += 1
            continue

        try:
            target_text, target = old_target(link)
            candidate = map_candidate(mapping, link, target, scan_root, new_root, normalized_old_root, keep_parts)
            new_target_text = format_target(link, candidate, target_text, target_style)
        except (OSError, ValueError) as e:
            logger.error("Cannot repair %s: %s", link, e)
            stats["failed"] += 1
            continue

        plans.append(RepairPlan(link, target_text, target, candidate, new_target_text))
        stats["planned"] += 1

    if not apply:
        for plan in plans:
            print(
                f"[DRY-RUN] {plan.link}: {plan.old_target_text} -> {plan.new_target_text} (candidate: {plan.candidate})"
            )
        return stats

    if stats["failed"]:
        logger.error("Planning failed; no links were changed")
        return stats

    stats["repaired"], apply_failures = apply_repair_plans(plans)
    stats["failed"] += apply_failures
    return stats


def materialize_link(link: Path, outpath: Path, *, method: str, apply: bool) -> None:
    if method not in MATERIALIZATION_METHODS:
        raise ValueError(f"unknown materialization method: {method}")

    target = link.resolve(strict=True)
    if not target.is_file():
        raise ValueError(f"symlink does not point to a regular file: {link} -> {target}")

    if not apply:
        print(f"[DRY-RUN] Would {method} {target} -> {outpath}")
        return

    if outpath != link:
        outpath.parent.mkdir(parents=True, exist_ok=True)
        if method == "hardlink":
            os.link(target, outpath)
        else:
            shutil.copy2(target, outpath, follow_symlinks=True)
        return

    fd, tmp_path_str = tempfile.mkstemp(prefix=f"{link.name}.", dir=str(link.parent))
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    cleanup = method == "copy"
    try:
        if method == "hardlink":
            tmp_path.unlink()
            os.link(target, tmp_path)
            cleanup = True
        else:
            shutil.copy2(target, tmp_path, follow_symlinks=True)
        tmp_path.replace(link)
        cleanup = False
    finally:
        if cleanup:
            tmp_path.unlink(missing_ok=True)


def materialize_links(
    roots: List[Path],
    output: Optional[Path],
    *,
    method: str,
    skip_existing: bool,
    recursive: bool,
    apply: bool,
    progress: Progress,
) -> None:
    output_root = absolute_path(output) if output is not None else None
    if output_root is not None and os.path.lexists(str(output_root)) and not output_root.is_dir():
        raise NotADirectoryError(f"output is not a directory: {output_root}")

    planned_destinations: Set[Path] = set()
    with progress.task() as task:
        for root in roots:
            scan_root = absolute_path(root)
            if not scan_root.is_dir():
                raise NotADirectoryError(f"scan root is not a directory: {scan_root}")

            for link in iter_symlinks(scan_root, recursive):
                task.update(description=link.name)
                destination = link if output_root is None else output_root / link.relative_to(scan_root)

                if output_root is not None and (
                    destination in planned_destinations or os.path.lexists(str(destination))
                ):
                    if skip_existing:
                        if not apply:
                            print(f"[DRY-RUN] Skipping {link} -> {destination}")
                        task.advance(delta=1)
                        continue
                    raise FileExistsError(f"{destination} already exists")

                planned_destinations.add(destination)
                materialize_link(link, destination, method=method, apply=apply)
                task.advance(delta=1)


def add_common_options(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Inspect subdirectories without following symlink or Windows reparse-point directories",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes; without this option, only print the plan",
    )


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Inspect, repair, or materialize symbolic links.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    repair = subparsers.add_parser(
        "repair",
        help="Retarget symlinks after their targets move",
        description="Retarget file or directory symlinks after their targets move.",
        epilog=REPAIR_EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    repair.add_argument("scan_root", type=Path, metavar="SCAN_ROOT", help="Directory containing symlinks to inspect")
    repair.add_argument("new_root", type=Path, metavar="NEW_ROOT", help="Existing root containing new targets")
    repair.add_argument(
        "--mapping",
        required=True,
        choices=MAPPING_MODES,
        help="How to map each old target or link location to a candidate below NEW_ROOT",
    )
    repair.add_argument(
        "--old-root",
        type=Path,
        help="Old target root to replace; required by root mapping and rejected by other mappings",
    )
    repair.add_argument(
        "--keep-parts",
        type=positive_int,
        help="Trailing target components to retain; required by tail mapping and rejected by other mappings",
    )
    repair.add_argument(
        "--target-style",
        choices=TARGET_STYLES,
        default="preserve",
        help="Store absolute or relative targets, or preserve each old link's style",
    )
    repair.add_argument(
        "--include-valid",
        action="store_true",
        help="Retarget valid symlinks as well as broken ones",
    )
    add_common_options(repair)

    materialize = subparsers.add_parser(
        "materialize",
        help="Turn file symlinks into copies or hardlinks",
        description="Create ordinary file entries from symlink targets, in place or under an output root.",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    materialize.add_argument(
        "scan_roots",
        nargs="+",
        type=Path,
        metavar="SCAN_ROOT",
        help="Directories containing file symlinks to inspect",
    )
    destination = materialize.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--in-place",
        action="store_true",
        help="Replace each symlink at its current path",
    )
    destination.add_argument(
        "--output",
        type=Path,
        metavar="OUTPUT_ROOT",
        help="Create files below this root and leave the source symlinks unchanged",
    )
    materialize.add_argument(
        "--method",
        choices=MATERIALIZATION_METHODS,
        default="copy",
        help="Create independent copies or same-volume hardlinks",
    )
    materialize.add_argument(
        "--skip-existing",
        action="store_true",
        help="With --output, skip destination paths that already exist",
    )
    add_common_options(materialize)
    return parser


def validate_args(parser: ArgumentParser, args: Namespace) -> None:
    if args.command == "repair":
        if args.mapping == "root" and args.old_root is None:
            parser.error("repair --mapping root requires --old-root")
        if args.mapping != "root" and args.old_root is not None:
            parser.error("repair --old-root can only be used with --mapping root")
        if args.mapping == "tail" and args.keep_parts is None:
            parser.error("repair --mapping tail requires --keep-parts")
        if args.mapping != "tail" and args.keep_parts is not None:
            parser.error("repair --keep-parts can only be used with --mapping tail")
    elif args.skip_existing and args.output is None:
        parser.error("materialize --skip-existing requires --output")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.command == "repair":
        try:
            stats = repair_links(
                args.scan_root,
                args.new_root,
                mapping=args.mapping,
                old_root=args.old_root,
                keep_parts=args.keep_parts,
                target_style=args.target_style,
                recursive=args.recursive,
                include_valid=args.include_valid,
                apply=args.apply,
            )
        except (OSError, ValueError) as e:
            logger.error("Repair failed: %s", e)
            return 1

        print("Summary: " + ", ".join(f"{name}={count}" for name, count in stats.items()))
        return 1 if stats["failed"] else 0

    try:
        with RichProgress() as rich_progress:
            materialize_links(
                args.scan_roots,
                args.output,
                method=args.method,
                skip_existing=args.skip_existing,
                recursive=args.recursive,
                apply=args.apply,
                progress=Progress(rich_progress),
            )
    except (OSError, ValueError) as e:
        logger.error("Materialization failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
