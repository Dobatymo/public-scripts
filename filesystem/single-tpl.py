from __future__ import generator_stop

from os import fspath
from pathlib import Path
from typing import TYPE_CHECKING

from genutility.file import read_file

if TYPE_CHECKING:
	from typing import FrozenSet

def esc(s):
	return s.replace("\\", "\\\\")

def main(inpath, tplpath, outpath, suffixes=frozenset(), prepend=""):
	# type: (Path, Path, Path, FrozenSet[str], str) -> None

	tpl = read_file(tplpath, "rt")
	assert isinstance(tpl, str) # for mypy

	paths = [path for path in inpath.iterdir() if path.suffix in suffixes]

	with open(outpath, "wt") as fw:
		fw.write(prepend.format(num=len(paths)))

		for i, path in enumerate(paths):
			fw.write(tpl.format(
				filename=path.name,
				efilename=esc(path.name),
				path=fspath(path),
				epath=esc(fspath(path)),
				stem=path.parent / path.stem, # path without suffix
				i=i
			))

if __name__ == "__main__":
	from argparse import ArgumentParser

	from genutility.args import future_file, is_dir, is_file, suffix

	parser = ArgumentParser(description="""Create a file based on a template for file in directory.
The template can contain the following Python format style placeholders: {filename}, {efilename}, {path}, {epath}, {stem}, {i}.
For example this can be used to create a shell script to include multiple video encode commands.""")
	parser.add_argument("inpath", type=is_dir, help="Directory with files")
	parser.add_argument("tplpath", type=is_file, help="Path to template file.")
	parser.add_argument("outpath", type=future_file, help="Path to output file.")
	parser.add_argument("--suffixes", nargs="+", default=[], type=suffix, help="Extensions to filter for.")
	prepend_group = parser.add_mutually_exclusive_group(required=False)
	prepend_group.add_argument("--prepend", default="", help="String to prepend to the output file once. Use {num} to include number of entries.")
	prepend_group.add_argument("--prependfile", type=is_file, help="File to prepend to the output file once. Use {num} to include number of entries.")
	args = parser.parse_args()

	if args.prependfile:
		prepend = read_file(args.prependfile, "rt")
	else:
		prepend = args.prepend

	assert isinstance(prepend, str) # for mypy
	main(args.inpath, args.tplpath, args.outpath, frozenset(args.suffixes), prepend)
