# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from pathlib import Path

def main(inpath):

	end = " - Verknüpfung.lnk"

	for path in Path(inpath).glob("*.lnk"):
		if path.name.endswith(end):
			path.rename(path.path[:-len(end)] + ".lnk")

if __name__ == "__main__":
	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("--inpath", default=".")
	args = parser.parse_args()

	main(args.inpath)
