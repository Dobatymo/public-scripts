import os, csv, re
from csv import DictReader
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from typing import Optional, Sequence

def main(inpath, csvfile, tpl, fields=None, key=None, regex=None, unsafe=False, dry=False):
	# type: (Path, Path, str, Optional[Sequence[str]], Optional[str], Optional[re.Pattern], bool) -> None

	csvdata = dict()
	with open(csvfile, "rt") as fr:
		csv = DictReader(fr, fieldnames=fields)
		if key:
			for row in csv:
				csvdata[row[key]] = row
		else:
			for i, row in enumerate(csv):
				csvdata[i] = row

	filedata = dict()
	if regex:
		for path in inpath.iterdir():
			keyval = regex.match(path.name).group(1)
			filedata[keyval] = path
	else:
		for i, path in enumerate(inpath.iterdir()):
			filedata[i] = path

	if not unsafe:
		if len(csvdata) != len(filedata):
			raise RuntimeError("Number of csv rows doesn't match number of files")

	for key, path in filedata.items():
		newname = tpl.format(**csvdata[key])
		newpath = path.with_name(newname)
		if dry:
			print(path, "->", newpath)
		else:
			path.rename(newpath)

if __name__ == "__main__":
	from argparse import ArgumentParser, RawDescriptionHelpFormatter
	from genutility.args import is_file, is_dir

	parser = ArgumentParser(description="""Example:
rename-with-csv.py "directory" "data.csv" "{char}.txt" --key num --regex "(.*)\.txt"
where data.csv file looks like
num,char
1,a
2,b
3,c

and directory contains files like this
1.txt
2.txt
3.txt""", formatter_class=RawDescriptionHelpFormatter)
	parser.add_argument("inpath", type=is_dir, help="Directory with files to rename")
	parser.add_argument("csvfile", type=is_file, help="CSV file to contain the rename information")
	parser.add_argument("tpl", help="Template string based on CSV field names")
	parser.add_argument("--fields", metavar="FIELD", nargs="+", help="Field names for CSV file. If not given, they will be detected automatically if possible.")
	parser.add_argument("--key", help="CSV field used to match csv rows to files. If missing, csv rows will be used in order.")
	parser.add_argument("--regex", type=re.compile, help="Regex to extract the key. If missing, files are renamed according to their alphabetical sort order.")
	parser.add_argument("--dry", action="store_true", help="Don't actually rename files, just print the renamings.")
	parser.add_argument("--unsafe", action="store_true", help="If set, the number of csv rows is not compared to the number of files")
	args = parser.parse_args()

	assert (args.key is None) == (args.regex is None), "Both key and regex or neither must be specified"

	main(args.inpath, args.csvfile, args.tpl, args.fields, args.key, args.regex, args.unsafe, args.dry)
