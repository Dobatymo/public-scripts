from __future__ import absolute_import, division, print_function, unicode_literals

from future.utils import viewitems
import logging
from collections import defaultdict
from hashlib import sha1

import numpy as np
from metrohash import MetroHash128
from PIL import Image

from genutility.hash import hash_file
from genutility.filesystem import scandir_rec, fileextensions, entrysuffix
from genutility.metrics import hamming_distance
from genutility.iter import progress
from genutility.metrictree import BKTree
from genutility.fingerprinting import phash_blockmean
from genutility.fileformats.jfif import hash_raw_jpeg
from genutility.fileformats.png import hash_raw_png
from genutility.hash import sha1_hash_file
from genutility.exceptions import ParseError

class Skip(Exception):
	pass

def metrohash(path):
	return hash_file(path, MetroHash128).digest()

def nometahash(path):
	lowerpath = path.lower()

	if lowerpath.endswith(".jpg") or lowerpath.endswith(".jpeg"):
		hashobj = sha1()
		try:
			hash_raw_jpeg(path, hashobj)
		except ParseError as e:
			logging.info("Skipping invalid JPEG file %s: %s", path, e)
			raise Skip()
		except EOFError:
			logging.info("Skipping truncated JPEG file %s", path)
			raise Skip()

		return hashobj.digest()

	elif lowerpath.endswith(".png"):
		hashobj = sha1()
		try:
			hash_raw_png(path, hashobj)
		except ParseError as e:
			logging.info("Skipping invalid PNG file %s: %s", path, e)
			raise Skip()
		except EOFError:
			logging.info("Skipping truncated PNG file %s", path)
			raise Skip()

		return hashobj.digest()

	else:
		raise Skip()

IGNORE_DIRNAMES = {".git"}

def iter_size_path(dirs):
	for dir in dirs:
		for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
			if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
				logging.debug("Skipped: %s", entry.path)
				entry.follow = False
			elif entry.is_file():
				filesize = entry.stat().st_size
				yield filesize, entry.path

def image_hash_tree(dirs, exts=None, processfunc=None):
	tree = BKTree(hamming_distance)
	map = defaultdict(list)
	exts = exts or fileextensions.images

	for dir in dirs:
		for entry in scandir_rec(dir, dirs=True, follow_symlinks=False, allow_skip=True):
			if entry.is_dir() and entry.name in IGNORE_DIRNAMES:
				logging.debug("Skipped: %s", entry.path)
				entry.follow = False
			elif entry.is_file():

				ext = entrysuffix(entry)[1:].lower()

				if ext in exts:
					try:
						img = Image.open(entry.path, "r")
					except OSError:
						logging.warning("Cannot open image: %s", entry.path)
					else:
						hash = phash_blockmean(img)
						tree.add(hash)
						map[hash].append(entry.path)
					if processfunc:
						processfunc((entry.path, hash))

	return tree, map

def iter_size_hashes(dups, hashfunc):
	for size, group in viewitems(dups):
		hashes = defaultdict(list)

		for path in group:
			try:
				hash = hashfunc(path)
			except PermissionError:
				logging.warning("Permission denied: %s", path)
			except FileNotFoundError:
				logging.warning("File not found: %s", path)
			else:
				hashes[hash].append(path)

		yield size, hashes

def dupgroups(dirs, hashfunc):
	dups = defaultdict(list)

	logging.info("Collecting files")
	for size, path in progress(iter_size_path(dirs)):
		dups[size].append(path)
	logging.info("Found %s size groups", len(dups))

	logging.info("Filtering files based on size")
	dups = {k: v for k, v in viewitems(dups) if len(v) > 1}
	logging.info("Found %s duplicate size groups", len(dups))

	logging.info("Calculating hash groups")
	for size, hashes in progress(iter_size_hashes(dups, hashfunc), length=len(dups)):
		dups[size] = hashes

	logging.info("Filtering files based on hashes")
	newdups = {}
	for size, sizegroup in viewitems(dups):
		for hash, hashgroup in viewitems(sizegroup):
			if len(hashgroup) > 1:
				newdups[(size, hash)] = hashgroup

	return newdups

def dupgroups_no_size(dirs, hashfunc):
	dups = defaultdict(list)

	logging.info("Calculating hash groups")
	for size, path in progress(iter_size_path(dirs)):
		try:
			hash = hashfunc(path)
		except PermissionError:
			logging.warning("Permission denied: %s", path)
		except FileNotFoundError:
			logging.warning("File not found: %s", path)
		except Skip:
			pass
		else:
			dups[hash].append((path, size))
	logging.info("Found %s hash groups", len(dups))

	logging.info("Filtering files based on hash")
	dups = {k: v for k, v in viewitems(dups) if len(v) > 1}
	logging.info("Found %s duplicate hash groups", len(dups))

	return dups

if __name__ == "__main__":

	import csv
	from argparse import ArgumentParser
	from genutility.file import StdoutFile
	from genutility.args import is_dir
	from tqdm import tqdm

	hashfuncs = {
		"metrohash": metrohash,
		"no-meta-sha1": nometahash,
	}

	parser = ArgumentParser()
	parser.add_argument("directories", type=is_dir, nargs="+", help="Directory to search")
	parser.add_argument("-o", "--out", help="outputfile")
	parser.add_argument("-v", "--verbose", action="store_true")
	parser.add_argument("--no-size", action="store_true")
	parser.add_argument("--hashfunc", default="metrohash", choices=hashfuncs.keys())

	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--exact", action="store_true")
	group.add_argument("--images", action="store_true")
	group.add_argument("--audio", action="store_true")

	args = parser.parse_args()

	if args.verbose:
		logging.basicConfig(level=logging.DEBUG)
	else:
		logging.basicConfig(level=logging.INFO)

	if args.exact:

		hashfunc = hashfuncs[args.hashfunc]

		if args.no_size:
			grous = dupgroups_no_size(args.directories, hashfunc)

			with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
				csvwriter = csv.writer(csvfile)
				csvwriter.writerow(["hash", "path", "size"])
				for hash, paths_sizes in viewitems(grous):
					for path, size in paths_sizes:
						csvwriter.writerow([hash.hex(), path, size])

		else:
			grous = dupgroups(args.directories, hashfunc)

			with StdoutFile(args.out, "xt", encoding="utf-8", newline="") as csvfile:
				csvwriter = csv.writer(csvfile)
				csvwriter.writerow(["size", "hash", "path"])
				for (size, hash), paths in viewitems(grous):
					for path in paths:
						csvwriter.writerow([size, hash.hex(), path])

	elif args.images:

		with StdoutFile(args.out, "xt", encoding="utf-8") as fw:

			with tqdm() as pbar:
				tree, map = image_hash_tree(args.directories, processfunc=lambda x: pbar.update(1))

			for d in range(100):
				groups = []

				for group in tree.find_by_distance(d):
					files = []
					for hash in group:
						files.extend(map[hash])
					groups.append(files)

				if groups:
					fw.write("Distance: {}\n".format(d))
					for group in groups:
						fw.write("{}\n".format(group))

					fw.write("---\n")

	elif args.audio:
		parser.exit("Duplicate search for audio is no implemented yet")
