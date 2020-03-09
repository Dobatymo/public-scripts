from __future__ import absolute_import, division, print_function, unicode_literals

import logging
from pathlib import Path

from mutagen.flac import FLAC

def modify_header(filetags, mapping):
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

def delete_flac_tags(directory, mapping):
	# type: (Path, Dict[str, Optional[str]]) -> None

	for path in Path(directory).rglob("*.flac"):
		if path.is_file():

			try:
				filetags = FLAC(filename)
				if modify_header(filetags, mapping):
					filetags.save()
			except Exception as e:
				logging.exception("Removing tags from %s failed.", filename)

if __name__ == "__main__":
	from argparse import ArgumentParser
	
	parser = ArgumentParser()
	parser.add_argument("paths", metavar="PATH", nargs="+", type=is_dir)
	args = parser.parse_args()

	for path in args.paths:
		delete_flac_tags(path)
