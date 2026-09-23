import logging
from pathlib import Path
from typing import Dict, Optional

from genutility.filesystem import scandir_ext
from mutagen import FileType
from mutagen.flac import FLAC


def modify_header(filetags: FileType, mapping: dict) -> bool:
    modified = False

    for k, v in mapping.items():
        if v:
            try:
                if filetags[k] == v:
                    del filetags[k]
                    modified = True
            except KeyError:
                pass
        else:
            try:
                del filetags[k]
                modified = True
            except KeyError:
                pass

    return modified


def delete_flac_tags(directory: Path, mapping: Dict[str, Optional[str]], follow_links: bool = False) -> None:
    for entry in scandir_ext(directory, include={".flac"}, follow_symlinks=follow_links):
        try:
            filetags = FLAC(entry.path)
            if modify_header(filetags, mapping):
                filetags.save()
        except Exception:
            logging.exception("Removing tags from %s failed.", entry.path)


if __name__ == "__main__":
    from argparse import ArgumentParser, ArgumentTypeError

    from genutility.args import is_dir, is_dir_nofollow

    parser = ArgumentParser()
    parser.add_argument("paths", metavar="PATH", nargs="+", type=Path)
    parser.add_argument("--key-value", action="append", nargs="+", metavar=("KEY", "VALUE=None"))
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")
    args = parser.parse_args()

    try:
        for path in args.paths:
            (is_dir if args.follow_links else is_dir_nofollow)(path)
    except ArgumentTypeError as e:
        parser.error(str(e))

    if not args.key_value:
        parser.error("--key-value not specified")

    map: Dict[str, Optional[str]] = {}
    for values in args.key_value:
        if len(values) == 1:
            map[values[0]] = None
        elif len(values) == 2:
            map[values[0]] = values[1]
        else:
            parser.error("--key-value must contain either the key or the key and value")

    print("Using header map", map)

    for path in args.paths:
        delete_flac_tags(path, map, args.follow_links)
