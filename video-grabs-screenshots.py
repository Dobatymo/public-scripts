from __future__ import absolute_import, division, print_function, unicode_literals

from pathlib import Path
from genutility.videofile import grab_pic
from genutility.args import suffix, is_dir

if __name__ == "__main__":

	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("inpath", type=is_dir)
	parser.add_argument("outpath", type=is_dir)
	parser.add_argument("--inext", type=suffix, default=".mp4")
	parser.add_argument("--outext", type=suffix, default=".jpg")
	parser.add_argument("--skip", action="store_true")
	args = parser.parse_args()

	for path_in in args.inpath.rglob("*" + args.inext):
		path_out = args.outpath / path_in.relative_to(args.inpath)
		path_out = path_out.with_suffix(args.outext)
		print(path_in, "->", path_out)
		if args.skip and path_out.exists(): # skip
			continue
		grab_pic(str(path_in), str(path_out))
