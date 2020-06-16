from __future__ import absolute_import, division, print_function, unicode_literals

import logging

logger = logging.getLogger(__name__)

if __name__ == "__main__":

	from argparse import ArgumentParser
	from genutility.args import suffix, existing_path, between
	from genutility.compat.pathlib import Path
	from genutility.filesystem import fileextensions
	from genutility.videofile import grab_pic, NoGoodFrame

	parser = ArgumentParser()
	parser.add_argument("inpath", type=existing_path)
	parser.add_argument("outpath", nargs="?", type=Path)
	parser.add_argument("-v", "--verbose", action="store_true", help="Enable debugging output")
	parser.add_argument("-f", "--format", type=suffix, choices=(".jpg", ".png", ".tiff"), default=".png", help="Picture format of the output file, if outpath is a directory.")
	parser.add_argument("-w", "--overwrite", action="store_true", help="Overwrite existing files")
	parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into subfolders if input is a directory.")

	parser.add_argument("--pos", nargs="*", default=[0.5], type=between(0, 1), help="List of of file position ratios")
	parser.add_argument("--backend", choices=("av", "cv"), default="cv")
	args = parser.parse_args()

	try:
		#from chromalog import ColorizingStreamHandler
		#logger.addHandler(ColorizingStreamHandler())
		import chromalog
		logmodule = chromalog
	except ImportError:
		from warnings import warn
		warn("Could not import chromalog, showing uncolored logs")
		logmodule = logging

	if args.verbose:
		logmodule.basicConfig(level=logging.DEBUG)
	else:
		logmodule.basicConfig(level=logging.INFO)


	if len(args.pos) == 1:
		pos = args.pos[0]

	if args.inpath.is_file():
		if args.outpath is None:
			path_out = args.outpath.with_suffix(args.format)
		else:
			if args.outpath.is_dir():
				raise ValueError("outpath cannot be a directory if inpath is a file")

			path_out = args.outpath

		grab_pic(args.inpath, path_out, pos, args.overwrite, args.backend)

	elif args.inpath.is_dir():
		video_suffixes = set("." + ext for ext in fileextensions.video)

		if args.recursive:
			it = args.inpath.rglob("*")
		else:
			it = args.inpath.glob("*")

		for path_in in it:
			if not path_in.is_file():
				continue

			if path_in.suffix.lower() not in video_suffixes:
				logger.debug("Skipping non-video file %s", path_in)
				continue

			if args.outpath is None:
				path_out = path_in.with_suffix(args.format)
			else:
				path_out = args.outpath / path_in.relative_to(args.inpath)
				path_out = path_out.with_suffix(args.format)

			logger.info("Processing %s", path_in)
			try:
				grab_pic(path_in, path_out, pos, args.overwrite, args.backend)
			except FileExistsError:
				logger.info("Skipping existing file %s", path_in)
			except NoGoodFrame as e:
				logger.warning("Could not grab frame from %s: %s", path_in, e)
	else:
		parser.error("inpath is neither file nor directory")
