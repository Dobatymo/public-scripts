from __future__ import absolute_import, division, print_function, unicode_literals

import logging

from genutility.compat.pathlib import Path
from genutility.videofile import grab_pic
from genutility.args import suffix, existing_path, between

if __name__ == "__main__":

	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("inpath", type=existing_path)
	parser.add_argument("outpath", type=Path) # , type=is_dir
	parser.add_argument("--inext", type=suffix, default=".mp4")
	parser.add_argument("--outext", type=suffix, default=".jpg")
	parser.add_argument("--backend", choices=("av", "cv"), default="cv")
	parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
	parser.add_argument("--pos", nargs="*", default=[0.5], type=between(0, 1), help="List of of file position ratios")
	args = parser.parse_args()

	if len(args.pos) == 1:
		pos = args.pos[0]

	if args.inpath.is_file():
		assert not args.outpath.is_dir()
		grab_pic(args.inpath, args.outpath, pos, args.overwrite, args.backend)

	else:
		for path_in in args.inpath.rglob("*" + args.inext):
			path_out = args.outpath / path_in.relative_to(args.inpath)
			path_out = path_out.with_suffix(args.outext)
			logging.info("Processing %s", path_in)
			try:
				grab_pic(path_in, path_out, pos, args.overwrite, args.backend)
			except FileExistsError:
				logging.info("Skipping existing file %s", path_in)
