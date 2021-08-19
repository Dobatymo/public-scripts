from __future__ import generator_stop

import logging
from typing import Iterable

from genutility.filesystem import is_writeable, make_writeable, scandir_rec
from genutility.stdio import confirm


def do(paths: Iterable[str], yes: bool) -> None:

	for path in paths:
		for entry in scandir_rec(path, dirs=False, files=True):
			stats = entry.stat()
			try:
				if not is_writeable(stats):
					if yes or confirm("Make {} writeable?".format(entry.path)):
						make_writeable(entry.path, stats)
						if yes:
							print("Made {} writeable".format(entry.path))
			except Exception:
				logging.exception(entry.path)

if __name__ == "__main__":

	from argparse import ArgumentParser
	parser = ArgumentParser()
	parser.add_argument("paths", metavar="PATH", nargs="+")
	parser.add_argument("-y", "--yes", action="store_true", help="yes to all")
	args = parser.parse_args()

	do(args.paths, args.yes)
