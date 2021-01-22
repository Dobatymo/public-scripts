from __future__ import generator_stop

from genutility.config import sort_config

if __name__ == "__main__":
	from argparse import ArgumentParser

	from genutility.args import future_file, is_file
	parser = ArgumentParser()
	parser.add_argument("inpath", type=is_file)
	parser.add_argument("outpath", type=future_file)
	args = parser.parse_args()

	sort_config(args.inpath, args.outpath)
