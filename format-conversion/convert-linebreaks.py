from __future__ import absolute_import, division, print_function, unicode_literals

if __name__ == "__main__":

	from argparse import ArgumentParser
	from io import open
	from genutility.filesystem import append_to_filename

	parser = ArgumentParser(description="Convert file to local linebreaks")
	parser.add_argument("infile")
	parser.add_argument("outfile", nargs="?", default=None)
	args = parser.parse_args()

	outfile = args.outfile or append_to_filename(args.infile, "converted")

	with open(args.infile, "rt") as fr:
		with open(args.outfile, "xt") as fw:
			fw.write(fr.read())
