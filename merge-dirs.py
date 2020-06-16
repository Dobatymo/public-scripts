import logging, os, errno

from genutility.compat import FileExistsError
from genutility.compat.os import fspath, replace
from genutility.exceptions import assert_choice

def remove_empty_error_log(path, e):
	if e.errno != errno.ENOTEMPTY:
		logging.warning("Failed to remove %s (%s)", path, e)

def remove_empty_dirs(path, ignore_errors=False, onerror=None):
	# type: (str, ) -> None

	for dirpath, dirnames, filenames in os.walk(path, topdown=False):
		if filenames:
			continue # skip remove

		try:
			os.rmdir(dirpath)
		except OSError as e:
			if ignore_errors:
				pass
			elif onerror:
				onerror(dirpath, e)
			else:
				raise

def move_rename(src, dst):
	raise RuntimeError("Not implemented")

MODES = ("fail", "no_move", "overwrite", "rename")

def merge(src, dst, mode="no_move"):

	assert_choice("mode", mode, MODES)

	if not src.is_dir() or not dst.is_dir():
		raise ValueError("src and dst must be directories")

	for path in src.rglob("*"):

		if path.is_dir():
			relpath = path.relative_to(src)
			(dst / relpath).mkdir(parents=True, exist_ok=True)
		elif path.is_file():
			relpath = path.relative_to(src)
			(dst / relpath.parent).mkdir(parents=True, exist_ok=True)
			target = dst / relpath
			if target.exists():
				if mode == "fail":
					raise FileExistsError(fspath(path))
				elif mode == "no_move":
					pass
				elif mode == "overwrite":
					replace(fspath(path), fspath(target))
				elif mode == "rename":
					move_rename(path, target)
			else:
				path.rename(target) # race condition on linux, us renameat2 with RENAME_NOREPLACE?
		else:
			raise RuntimeError("Unhandled file: {}".format(path))

	remove_empty_dirs(fspath(src), onerror=remove_empty_error_log)

if __name__ == "__main__":

	from argparse import ArgumentParser
	from genutility.args import is_dir

	parser = ArgumentParser()
	parser.add_argument("src", type=is_dir, help="Source directory")
	parser.add_argument("dst", type=is_dir, help="Target directory")
	parser.add_argument("--mode", choices=MODES, default="no_move", help="Specifies the handling of files in src which already exist in dst.")
	args = parser.parse_args()

	merge(args.src, args.dst, args.mode)
