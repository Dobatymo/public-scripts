from __future__ import absolute_import, division, print_function, unicode_literals

from genutility.compat.pathlib import Path
from genutility.videofile import grab_pic
from genutility.args import suffix, existing_path

if __name__ == "__main__":

	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("inpath", type=existing_path)
	parser.add_argument("outpath", type=Path) # , type=is_dir
	parser.add_argument("--inext", type=suffix, default=".mp4")
	parser.add_argument("--outext", type=suffix, default=".jpg")
	parser.add_argument("--skip", action="store_true")
	parser.add_argument("--backend", choices=("av", "cv"), default="av")
	args = parser.parse_args()

	if args.inpath.is_file():
		assert not args.outpath.is_dir()
		grab_pic(args.inpath, args.outpath, backend=args.backend)

	else:
		for path_in in args.inpath.rglob("*" + args.inext):
			path_out = args.outpath / path_in.relative_to(args.inpath)
			path_out = path_out.with_suffix(args.outext)
			print(path_in, "->", path_out)
			if args.skip and path_out.exists(): # skip
				continue
			grab_pic(path_in, path_out, backend=args.backend)
