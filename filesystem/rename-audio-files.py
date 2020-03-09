from __future__ import unicode_literals, print_function
import os, os.path

from genutility.mediainfo import MediaInfoHelper
from genutility.deprecated_filesystem import listdir_rec
from genutility.filesystem import windows_compliant_filename

def rename_audio_files(basepath, tpl):

	for path in listdir_rec(basepath, False):
		mi = MediaInfoHelper(path)
		meta = mi.meta_info()

		title = windows_compliant_filename(meta.get('title', 'Unknown'))
		album = windows_compliant_filename(meta.get('album', 'Unknown'))
		artist = windows_compliant_filename(meta.get('performer', 'Unknown'))
		date = windows_compliant_filename(meta.get('date', '"xx.xx.xxxx"'))
		filename = os.path.basename(path)

		newpath = os.path.join(basepath, tpl.format(
			title=title,
			album=album,
			artist=artist,
			date=date,
			filename=filename
		))

		if path != newpath:
			print(path + "\n" + newpath + "\n")
			try:
				os.renames(path, newpath)
			except Exception as e:
				print(e)

if __name__ == "__main__":

	from argparse import ArgumentParser()

	parser = ArgumentParser()
	parser.add_argument("path")
	parser.add_argument("tpl", help="eg. '{album} - {date}/{filename}'")
	args = parser.parse_args()

	rename_audio_files(args.path, args.tpl)
