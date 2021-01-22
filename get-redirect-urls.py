from __future__ import generator_stop

import logging
import sys

from genutility.compat.contextlib import nullcontext
from genutility.http import URLRequest


def check_context(context):
	with context as fr:
		for line in fr:
			link = line.rstrip()
			try:
				url = URLRequest(link).get_redirect_url()
				print(url)
			except Exception as e:
				logging.exception(link)

if __name__ == "__main__":

	from argparse import ArgumentParser
	parser = ArgumentParser()
	parser.add_argument("file", nargs="?", default="-", help="file which contains one link per line. `-` reads from stdin.")
	args = parser.parse_args()

	if args.file == "-":
		context = nullcontext(sys.stdin)
	else:
		context = open(args.file, "rt", encoding="utf-8")

	check_context(context)
