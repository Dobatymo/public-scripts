from __future__ import absolute_import, division, print_function, unicode_literals

import sys, os.path, shutil, subprocess, tempfile

from genutility.twothree.filesystem import tofs
from genutility.stdio import errorquit, waitquit

from argparse import ArgumentParser

if __name__ == "__main__":

	parser = ArgumentParser()
	parser.add_argument("path")
	args = parser.parse_args()

	try:
		tmpName = "open-copy.tmp"
		tmpPath = os.path.join(tempfile.gettempdir(), tempfile.gettempprefix() + tmpName)
		shutil.copy(args.path, tmpPath)
		errorquit(subprocess.call(tofs(tmpPath), shell=True))
	except Exception as e:
		waitquit(exception=e)
