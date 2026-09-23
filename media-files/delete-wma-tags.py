from pathlib import Path
from typing import Iterable

from genutility.filesystem import scandir_rec
from genutility.stdio import waitcontinue
from mutagen.asf import ASF


def delete_tags_from_wma(path: str, tags: Iterable[str]) -> bool:
    asf = ASF(path)
    modified = False

    if asf.tags is not None:
        for tag in tags:
            try:
                asf.tags.pop(tag)
                modified = True
            except KeyError:
                pass
        if modified:
            asf.save()

    return modified


if __name__ == "__main__":
    from argparse import ArgumentParser, ArgumentTypeError

    from genutility.args import is_dir, is_dir_nofollow
    from genutility.ops import logical_xor

    parser = ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--tags", nargs="*")
    parser.add_argument("--napster", action="store_true")
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")
    args = parser.parse_args()

    try:
        (is_dir if args.follow_links else is_dir_nofollow)(args.path)
    except ArgumentTypeError as e:
        parser.error(str(e))

    if not logical_xor(args.napster, args.tags):
        parser.error("Either --napster or --tags must be specified")

    if args.napster:
        tags = ["WM/NapsterHeader"]
    else:
        tags = args.tags

    for entry in scandir_rec(args.path, dirs=False, files=True, follow_symlinks=args.follow_links):
        if entry.path.endswith(".wma"):
            try:
                if delete_tags_from_wma(entry.path, tags):
                    print(f"Deleted tags from {entry.path}")
            except Exception as e:
                waitcontinue(entry.path, exception=e)
