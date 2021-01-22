from __future__ import generator_stop

from pathlib import Path
from typing import TYPE_CHECKING

from genutility.file import read_file

if TYPE_CHECKING:
	from typing import FrozenSet

def esc(s):
	return s.replace("\\", "\\\\")

def main(inpath, tplpath, outpath, outsuffix, suffixes=frozenset()):
	# type: (Path, Path, Path, str, FrozenSet[str]) -> None

	tpl = read_file(tplpath, "rt")

	for i, path in enumerate(path for path in inpath.iterdir() if path.suffix in suffixes):
		with open(outpath / path.with_suffix(outsuffix).name, "wt") as fw:
			fw.write(tpl.format(
				filename=path.name,
				efilename=esc(path.name),
				path=path.path,
				epath=esc(path.path),
				stem=path.parent / path.stem, # path without suffix
				i=i
			))

if __name__ == "__main__":
	from argparse import ArgumentParser

	from genutility.args import is_dir, is_file, suffix

	parser = ArgumentParser(description="""Create a file for file in directory based on a template file.
The template can contain the following Python format style placeholders: {filename}, {efilename}, {path}, {epath}, {stem}, {i}.
For example this can be used to create one AviSynth script file per video file.""")
	parser.add_argument("inpath", type=is_dir, help="Directory with files.")
	parser.add_argument("tplpath", type=is_file, help="Path to template file.")
	parser.add_argument("outsuffix", type=suffix, help="Suffix of created files.")
	parser.add_argument("--outpath", type=is_dir, default=Path("."), help="Directory where created files are placed.")
	parser.add_argument("--suffixes", metavar="SUFFIX", nargs="+", default=[], type=suffix, help="Extensions to filter for.")
	args = parser.parse_args()

	main(args.inpath, args.tplpath, args.outpath, frozenset(args.suffixes))
