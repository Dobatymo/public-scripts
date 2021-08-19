from __future__ import generator_stop

import logging
from os import fspath
from pathlib import Path
from typing import Dict, Optional

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

def delete_flac_tags(directory: Path, mapping: Dict[str, Optional[str]]) -> None:

	for path in Path(directory).rglob("*.flac"):
		if path.is_file():

			try:
				filetags = FLAC(fspath(path))
				if modify_header(filetags, mapping):
					filetags.save()
			except Exception:
				logging.exception("Removing tags from %s failed.", path)

if __name__ == "__main__":
	from argparse import ArgumentParser

	from genutility.args import is_dir

	parser = ArgumentParser()
	parser.add_argument("paths", metavar="PATH", nargs="+", type=is_dir)
	parser.add_argument("--key-value", action="append", nargs="+", metavar=("KEY", "VALUE=None"))
	args = parser.parse_args()

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
		delete_flac_tags(path, map)
