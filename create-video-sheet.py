from __future__ import absolute_import, division, print_function, unicode_literals

import logging
from datetime import timedelta

from genutility.image import resize_oar
from genutility.indexing import to_2d_index
from genutility.iter import iter_except
from genutility.videofile import AvVideo, CvVideo, NoGoodFrame

from PIL import Image, ImageDraw, ImageFont

try:
	av = AvVideo.import_backend()
	BackendCls = AvVideo
	av.logging.restore_default_callback()
	logging.warning("Using av backend")

except ImportError:
	cv2 = CvVideo.import_backend()
	BackendCls = CvVideo
	logging.warning("Using cv2 backend")

class EveryX(BackendCls):

	def __init__(self, path, seconds):
		BackendCls.__init__(self, path)
		self.seconds = seconds

	def calculate_step(self, time_base, duration):
		# type: (Fraction, int) -> int

		return self.seconds * time_base.denominator // time_base.numerator

class FramesX(BackendCls):

	def __init__(self, path, frames):
		BackendCls.__init__(self, path)
		self.frames = frames

	def calculate_step(self, time_base, duration):
		# type: (Fraction, int) -> int

		if self.frames == 1:
			return None
		return duration // (self.frames - 1)

def create_sheet(frames, dar, cols, rows, maxthumbsize=(128, 128), padding=(5, 5), background="black",
	textcolor="white", timestamp=True, fontfile="arial.ttf", fontsize=10):

	assert dar, "DAR not given"
	assert rows, "Variable row count not implement yet"

	ttf = ImageFont.truetype(fontfile, fontsize)

	max_width, max_height = maxthumbsize
	pad_width, pad_height = padding

	thumb_width, thumb_height = resize_oar(max_width, max_height, dar)

	sheet_width = thumb_width * cols + pad_width * (cols + 1)
	sheet_height = thumb_height * rows + pad_height * (rows + 1)

	grid = Image.new("RGB", (sheet_width, sheet_height), background)
	d = ImageDraw.Draw(grid)

	def no_good_frame(iterator, e):
		logging.warning("Video file was not processed completely: %s", e)

	for i, (frametime, frame) in enumerate(iter_except(frames, {NoGoodFrame: no_good_frame}, True)):

		if isinstance(frame, Exception):
			logging.warning("Skipping frame %s (%s)", frametime, frame)
			continue

		image = Image.fromarray(frame)

		thumbnail = image.resize((thumb_width, thumb_height), Image.BICUBIC) # or Image.LANCZOS?

		td = timedelta(seconds=frametime)

		if i == rows * cols:
			raise RuntimeError("more image extracted than needed")

		y, x = to_2d_index(i, cols)

		grid_x = thumb_width * x + pad_width * (x + 1)
		grid_y = thumb_height * y + pad_height * (y + 1)
		grid.paste(thumbnail, (grid_x, grid_y))
		if timestamp:
			d.text((grid_x, grid_y), str(td), fill=textcolor, font=ttf)

	return grid

def main(inpath, outpath, cols, rows=None, seconds=None, args=None, dry=False):

	if rows:
		context = FramesX(inpath, cols * rows)
	elif seconds:
		context = EveryX(inpath, seconds)

	with context as video:
		dar = video.meta["display_aspect_ratio"]
		sheet = create_sheet(video.iterate(), dar, cols, rows, args["thumbsize"], args["padding"],
			args["background"], args["textcolor"], args["timestamp"], args["fontfile"], args["fontsize"])

		if not dry:
			sheet.save(outpath, quality=args["quality"])

if __name__ == "__main__":

	from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter

	from genutility.compat.pathlib import Path
	from genutility.args import existing_path, in_range, abs_path
	from genutility.filesystem import fileextensions

	parser = ArgumentParser(description="Create video sheet / grid of thumbnails from video file.", formatter_class=ArgumentDefaultsHelpFormatter)
	parser.add_argument("inpath", type=existing_path, help="Path to input video file")
	parser.add_argument("outpath", nargs="?", type=abs_path, help="Path to output image file")
	parser.add_argument("-v", "--verbose", action="store_true", help="Enable debugging output")
	parser.add_argument("-w", "--overwrite", action="store_true", help="Overwrite existing files")
	parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into subfolders if input is a directory.")
	parser.add_argument("-f", "--format", choices=(".jpg", ".png", ".webp", ".tiff"), default=".jpg", help="Picture format of the output file, if outpath is a directory.")

	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--colsrows", nargs=2, metavar=("C", "R"), type=int, help="Create sheet with C columns and R rows. Thumbnails will be equally spaced timewise.")
	group.add_argument("-s", "--seconds", metavar="N", type=int, help="Grab a video frame every N seconds")
	parser.add_argument("--cols", metavar="C", type=int, help="Number of columns")

	parser.add_argument("--thumbsize", nargs=2, metavar=("W", "H"), type=int, default=(250, 250), help="Maximum dimensions of thumbnails")
	parser.add_argument("--padding", nargs=2, metavar=("W", "H"), type=int, default=(5, 5), help="Padding between thumbnails")
	parser.add_argument("--background", type=str, default="black", help="Background color")
	parser.add_argument("--textcolor", type=str, default="white", help="Text color")
	parser.add_argument("--timestamp", action="store_true", help="Include timestamp in thumbnails")
	parser.add_argument("--fontfile", default="arial.ttf", help="Path to truetype font file")
	parser.add_argument("--fontsize", type=int, default=10, help="Fontsize")
	parser.add_argument("-q", "--quality", type=in_range(1, 101), default=80, help="JPEG output quality, ignored if outpath is not .jpg")
	parser.add_argument("-d", "--dry", action="store_true", help="If set, no files are actually created")
	args = parser.parse_args()

	argsdict = {
		"thumbsize": args.thumbsize,
		"padding": args.padding,
		"background": args.background,
		"textcolor": args.textcolor,
		"timestamp": args.timestamp,
		"fontfile": args.fontfile,
		"fontsize": args.fontsize,
		"quality": args.quality,
	}

	try:
		import chromalog
		logmodule = chromalog
	except ImportError:
		logmodule = logging

	"""try:
		import coloredlogs
		coloredlogs.install(fmt="%(asctime)s,%(msecs)03d %(hostname)s %(name)s[%(process)d] %(levelname)s %(message)s")
	except ImportError:
		pass"""

	if args.verbose:
		logmodule.basicConfig(level=logging.DEBUG)
	else:
		logmodule.basicConfig(level=logging.INFO)

	if args.colsrows:
		cols, rows = args.colsrows
		seconds = None

	elif args.seconds:
		if not args.cols:
			parser.error("--seconds xnor --cols must be given")

		cols = args.cols
		rows = None
		seconds = args.seconds

	if args.inpath.is_file():

		if args.outpath is None:
			outpath = args.inpath.with_suffix(args.format)
		else:
			outpath = args.outpath

		main(args.inpath, outpath, cols, rows, seconds, argsdict, args.dry)

	elif args.inpath.is_dir():

		video_suffixes = set("." + ext for ext in fileextensions.video)

		if args.recursive:
			it = args.inpath.rglob("*")
		else:
			it = args.inpath.glob("*")

		for inpath in it:
			if not inpath.is_file():
				continue

			if inpath.suffix not in video_suffixes:
				logging.debug("Skipping non-video file %s", inpath)
				continue

			if args.outpath is None:
				outpath = inpath.with_suffix(args.format)
			else:
				outpath = args.outpath / inpath.parent.relative_to(args.inpath)
				outpath.mkdir(parents=True, exist_ok=True)
				outpath = outpath / Path(inpath.name).with_suffix(args.format)

			if args.overwrite or not outpath.exists():
				logging.info("Processing %s", inpath)

				try:
					main(inpath, outpath, cols, rows, seconds, argsdict, args.dry)
				except NoGoodFrame:
					logging.warning("Skipping broken file %s", inpath)
				except Exception:
					logging.exception("Skipping %s", inpath)
			else:
				logging.info("Skipping existing file %s", inpath)

	else:
		parser.error("inpath is neither file nor directory")
