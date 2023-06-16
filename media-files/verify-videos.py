import logging
import os

from genutility.av import BadFile, CorruptFile, iter_video
from genutility.iter import consume, progress

if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import is_dir
    from genutility.filesystem import fileextensions

    parser = ArgumentParser()
    parser.add_argument("inpath", type=is_dir, help="Path to video files")
    parser.add_argument("-r", "--recursive", action="store_true")
    args = parser.parse_args()

    video_suffixes = {"." + ext for ext in fileextensions.video}

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

        def info(i, length):
            return str(inpath.name)[:60]

        try:
            consume(progress(iter_video(os.fspath(inpath)), extra_info_callback=info))
        except (CorruptFile, BadFile) as e:
            logging.warning("%s is broken: %s", inpath, e)
        except Exception:
            logging.exception("%s is broken", inpath)
