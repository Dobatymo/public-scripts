from __future__ import unicode_literals, print_function

import logging
from genutility.filesystem import scandir_rec, is_writeable, make_writeable
from genutility.stdio import confirm

def do(paths, yes):
	# type: (Iterable[str], bool) -> None

	for path in paths:
		for entry in scandir_rec(path, dirs=False, files=True):
			stats = entry.stat()
			try:
				if not is_writeable(stats):
					if yes or confirm("Make {} writeable?".format(entry.path)):
						make_writeable(entry.path, stats)
						if yes:
							print("Made {} writeable".format(entry.path))
			except Exception as e:
				logging.exception(entry.path)

if __name__ == "__main__":

	from argparse import ArgumentParser
	parser = ArgumentParser()
	parser.add_argument("paths", metavar="PATH", nargs="+")
	parser.add_argument("-y", "--yes", action="store_true", help="yes to all")
	args = parser.parse_args()

	do(args.paths, args.yes)
