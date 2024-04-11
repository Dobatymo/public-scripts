import logging
import platform
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from datetime import timedelta
from fractions import Fraction
from math import ceil, floor
from os import fspath
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from genutility.args import abs_path, existing_path, in_range, suffix
from genutility.filesystem import fileextensions, mdatetime
from genutility.image import resize_oar
from genutility.indexing import to_2d_index
from genutility.iter import iter_except
from genutility.math import byte2size_str
from genutility.pillow import multiline_textsize
from genutility.rich import MarkdownHighligher, Progress
from genutility.videofile import AvVideo, CvVideo, NoGoodFrame
from PIL import Image, ImageDraw, ImageFont
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress

if platform.system() == "Linux":
    DEFAULT_FONTFILE = "LiberationSans-Regular.ttf"
else:
    DEFAULT_FONTFILE = "arial.ttf"
DEFAULT_FONTSIZE = 10
DEFAULT_TEMPLATE = (
    "File name: {filename}\nFile size: {filesize_bytes} bytes\nResolution: {width}x{height}\nDuration: {duration}"
)
DEFAULT_PADDING = (5, 5)

logger = logging.getLogger(__name__)


def make_seconds(BackendCls):
    class EveryX(BackendCls):
        def __init__(self, path: str, seconds: float) -> None:
            BackendCls.__init__(self, path)
            if seconds <= 0.0:
                raise ValueError("seconds must be larger than 0")
            self.seconds = seconds

        def _frame_range(self, time_base: Fraction, duration: int) -> range:
            steps = floor(duration * time_base / self.seconds)
            return range(0, steps + 1)

        def calculate_offsets(self, time_base: Fraction, duration: int) -> Iterator[int]:
            for i in self._frame_range(time_base, duration):
                yield int(i * self.seconds / time_base)

    return EveryX


def make_frames(BackendCls):
    class FramesX(BackendCls):
        def __init__(self, path: str, frames: int, include_sides: bool = False) -> None:
            """if include_sides is True, the first and last frame will be included."""

            BackendCls.__init__(self, path)
            if frames <= 0:
                raise ValueError("frames must be larger than 0")
            self.frames = frames
            self.include_sides = include_sides

        def _frame_range(self, time_base: Fraction, duration: int) -> range:
            if self.include_sides:
                part_offset = 0
            else:
                part_offset = 1

            return range(part_offset, self.frames + part_offset)

        def calculate_offsets(self, time_base: Fraction, duration: int) -> Iterator[int]:
            duration_incl = duration - 1

            if self.include_sides:
                parts = self.frames - 1
            else:
                parts = self.frames + 1

            for i in self._frame_range(time_base, duration):
                yield int(i * duration_incl / parts)

    return FramesX


def create_header(path: Path, meta: Dict, template: Optional[str] = None) -> str:
    filesize = path.stat().st_size

    template = template or DEFAULT_TEMPLATE

    fkwargs = {
        "path": fspath(path),
        "filename": path.name,
        "filesize_human": byte2size_str(filesize),
        "filesize_bytes": filesize,
        "modtime": mdatetime(path),
        "duration": meta["duration"],
        "width": meta["width"],
        "height": meta["height"],
        "fps": float(meta["fps"]),
        "dar": meta["display_aspect_ratio"],
    }

    return template.format_map(fkwargs)


def calc_sheet_size(
    cols: int,
    rows: int,
    thumb_width: int,
    thumb_height: int,
    pad_width: int,
    pad_height: int,
) -> Tuple[int, int]:
    sheet_width = thumb_width * cols + pad_width * (cols + 1)
    sheet_height = thumb_height * rows + pad_height * (rows + 1)

    return sheet_width, sheet_height


def _create_sheet(
    grid,
    frames,
    cols,
    rows,
    thumb_width,
    thumb_height,
    pad_width,
    pad_height,
    textcolor,
    ttf,
    y_offset=0,
    timestamp=True,
):
    d = ImageDraw.Draw(grid)

    def no_good_frame(iterator, e):
        logger.warning("Video file was not processed completely: %s", e)

    for i, (frametime, frame) in enumerate(iter_except(frames, {NoGoodFrame: no_good_frame}, True)):
        if isinstance(frame, Exception):
            logger.warning("Skipping frame %s (%s)", frametime, frame)
            continue

        image = Image.fromarray(frame)

        thumbnail = image.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)

        td = timedelta(seconds=frametime)

        # if timestamp:
        # 	from genutility.pillow import write_text
        # 	write_text(thumbnail, td.isoformat(), align, textcolor, textoutlinecolor, fontratio, padding=(1, 1))

        if i >= rows * cols:
            logger.warning("more images extracted than needed")
            # raise RuntimeError("more images extracted than needed")

        y, x = to_2d_index(i, cols)

        grid_x = thumb_width * x + pad_width * (x + 1)
        grid_y = thumb_height * y + pad_height * (y + 1) + y_offset
        grid.paste(thumbnail, (grid_x, grid_y))
        if timestamp:
            d.text((grid_x, grid_y), str(td), fill=textcolor, font=ttf)

    return grid


def create_sheet(
    frames,
    dar,
    cols,
    rows,
    maxthumbsize=(128, 128),
    padding=DEFAULT_PADDING,
    background="black",
    textcolor="white",
    ttf=None,
    timestamp=True,
    headertext=None,
    spacing=4,
):
    if not rows:
        raise ValueError("Variable row count not implement yet")

    max_width, max_height = maxthumbsize
    pad_width, pad_height = padding
    thumb_width, thumb_height = resize_oar(max_width, max_height, dar)
    sheet_width, sheet_height = calc_sheet_size(cols, rows, thumb_width, thumb_height, pad_width, pad_height)

    if headertext:
        header_width, header_height = multiline_textsize(headertext, ttf, spacing)
    else:
        header_width, header_height = 0, 0

    grid = Image.new("RGB", (sheet_width, sheet_height + header_height), background)

    if header_width > grid.width:
        logger.info("Header text will be cut off")

    if headertext:
        d = ImageDraw.Draw(grid)
        d.multiline_text((0, 0), headertext, fill=textcolor, font=ttf, spacing=spacing)

    return _create_sheet(
        grid,
        frames,
        cols,
        rows,
        thumb_width,
        thumb_height,
        pad_width,
        pad_height,
        textcolor,
        ttf,
        header_height,
        timestamp,
    )


def create_sheet_cli(
    inpath,
    outpath,
    cols: int,
    rows: Optional[int],
    seconds: Optional[float],
    args: Dict,
    progress: Progress,
    backend: Optional[str] = None,
    dry: bool = False,
):
    BackendCls: type

    if backend is None:
        try:
            CvVideo.import_backend()
            BackendCls = CvVideo
            logger.debug("Using cv2 backend")
        except ImportError:
            AvVideo.import_backend()
            BackendCls = AvVideo
            logger.debug("Using av backend")
    elif backend == "av":
        AvVideo.import_backend()
        BackendCls = AvVideo
    elif backend == "cv":
        CvVideo.import_backend()
        BackendCls = CvVideo
    else:
        raise ValueError(f"Invalid backend: {backend}")

    if rows:
        context = make_frames(BackendCls)(inpath, cols * rows)
    elif seconds:
        context = make_seconds(BackendCls)(inpath, seconds)

    if args["header"] or args["timestamp"]:
        fontfile = args.get("fontfile", DEFAULT_FONTFILE)
        fontsize = args.get("fontsize", DEFAULT_FONTSIZE)
        ttf = ImageFont.truetype(fontfile, fontsize)
    else:
        ttf = None

    with context as video:
        dar = video.meta["display_aspect_ratio"]
        num_frames = len(video.frame_range())

        if rows:
            pass
        elif seconds:
            rows = ceil(num_frames / cols)

        if args["header"]:
            headertext = create_header(inpath, video.meta)
        else:
            headertext = None

        sheet = create_sheet(
            progress.track(video.iterate(), total=num_frames, description="Reading frames"),
            dar,
            cols,
            rows,
            args["thumbsize"],
            args["padding"],
            args["background"],
            args["textcolor"],
            ttf,
            args["timestamp"],
            headertext,
        )

        if not dry:
            sheet.save(outpath, quality=args["quality"])


def main():
    parser = ArgumentParser(
        description="Create video sheet / grid of thumbnails from video file.",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("inpath", type=existing_path, help="Path to input video file")
    parser.add_argument("outpath", nargs="?", type=abs_path, help="Path to output image file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debugging output")
    parser.add_argument("-w", "--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Recurse into subfolders if input is a directory.",
    )
    parser.add_argument(
        "-f",
        "--format",
        type=suffix,
        choices=(".jpg", ".png", ".webp", ".tiff"),
        default=".jpg",
        help="Picture format of the output file, if outpath is a directory.",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--colsrows",
        nargs=2,
        metavar=("C", "R"),
        type=int,
        help="Create sheet with C columns and R rows. Thumbnails will be equally spaced timewise.",
    )
    group.add_argument(
        "-s",
        "--seconds",
        metavar="N",
        type=float,
        help="Grab a video frame every N seconds",
    )
    parser.add_argument("--cols", metavar="C", type=int, help="Number of columns")

    parser.add_argument(
        "-e",
        "--header",
        action="store_true",
        help="Add file meta information to the sheet.",
    )
    parser.add_argument(
        "--thumbsize",
        nargs=2,
        metavar=("W", "H"),
        type=int,
        default=(250, 250),
        help="Maximum dimensions of thumbnails",
    )
    parser.add_argument(
        "--padding",
        nargs=2,
        metavar=("W", "H"),
        type=int,
        default=DEFAULT_PADDING,
        help="Padding between thumbnails",
    )
    parser.add_argument("--background", type=str, default="black", help="Background color")
    parser.add_argument("--textcolor", type=str, default="white", help="Text color")
    parser.add_argument("-t", "--timestamp", action="store_true", help="Include timestamp in thumbnails")
    parser.add_argument("--fontfile", default=DEFAULT_FONTFILE, help="Path to truetype font file")
    parser.add_argument("--fontsize", type=int, default=DEFAULT_FONTSIZE, help="Fontsize")
    parser.add_argument(
        "--quality",
        type=in_range(1, 101),
        default=80,
        help="JPEG output quality, ignored if outpath is not .jpg",
    )
    parser.add_argument("-d", "--dry", action="store_true", help="If set, no files are actually created")
    parser.add_argument(
        "--backend",
        choices=("cv", "av"),
        help="Chose between pyav and opencv backend. If not set, first cv and than av is tried.",
    )
    args = parser.parse_args()

    argsdict = {
        "header": args.header,
        "thumbsize": args.thumbsize,
        "padding": args.padding,
        "background": args.background,
        "textcolor": args.textcolor,
        "timestamp": args.timestamp,
        "fontfile": args.fontfile,
        "fontsize": args.fontsize,
        "quality": args.quality,
    }

    handler = RichHandler(log_time_format="%Y-%m-%d %H-%M-%S%Z", highlighter=MarkdownHighligher())
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    if args.colsrows:
        cols, rows = args.colsrows
        seconds = None

    elif args.seconds:
        if not args.cols:
            parser.error("when --seconds is used, --cols must be given")

        cols = args.cols
        rows = None
        seconds = args.seconds

    if args.inpath.is_file():
        if args.outpath is None:
            outpath = args.inpath.with_suffix(args.format)
        else:
            assert not args.outpath.is_dir()
            outpath = args.outpath

        with RichProgress() as p:
            progress = Progress(p)
            create_sheet_cli(args.inpath, outpath, cols, rows, seconds, argsdict, progress, args.backend, args.dry)

    elif args.inpath.is_dir():
        video_suffixes = {"." + ext for ext in fileextensions.video}

        if args.recursive:
            it = args.inpath.rglob("*")
        else:
            it = args.inpath.glob("*")

        with RichProgress() as p:
            progress = Progress(p)
            for inpath in progress.track(it, description="Reading files"):
                if not inpath.is_file():
                    continue

                if inpath.suffix.lower() not in video_suffixes:
                    logger.debug("Skipping non-video file %s", inpath)
                    continue

                if args.outpath is None:
                    outpath = inpath.with_suffix(args.format)
                else:
                    outpath = args.outpath / inpath.parent.relative_to(args.inpath)
                    outpath.mkdir(parents=True, exist_ok=True)
                    outpath = outpath / Path(inpath.name).with_suffix(args.format)

                if args.overwrite or not outpath.exists():
                    logger.info("Processing %s", inpath)

                    # filerelpath = inpath.relative_to(args.inpath)
                    try:
                        create_sheet_cli(
                            inpath, outpath, cols, rows, seconds, argsdict, progress, args.backend, args.dry
                        )
                    except NoGoodFrame:
                        logger.warning("Skipping broken file %s", inpath)
                    except Exception:
                        logger.exception("Skipping %s", inpath)
                else:
                    logger.info("Skipping existing file %s", inpath)

    else:
        parser.error("inpath is neither file nor directory")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
