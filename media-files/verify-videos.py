import logging

from genutility.av import BadFile, CorruptFile, iter_video
from genutility.filesystem import entrysuffix, scandir_rec
from genutility.iter import consume, progress

if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import is_dir
    from genutility.filesystem import fileextensions

    parser = ArgumentParser()
    parser.add_argument("inpath", type=is_dir, help="Path to video files")
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("--follow-links", action="store_true", help="Follow file and directory links during scanning")
    args = parser.parse_args()

    video_suffixes = {"." + ext for ext in fileextensions.video}

    for entry in scandir_rec(
        args.inpath,
        dirs=False,
        rec=args.recursive,
        follow_symlinks=args.follow_links,
    ):
        if entrysuffix(entry) not in video_suffixes:
            logging.debug("Skipping non-video file %s", entry.path)
            continue

        def info(i, length):
            return entry.name[:60]  # noqa: B023

        try:
            consume(progress(iter_video(entry.path), extra_info_callback=info))
        except (CorruptFile, BadFile) as e:
            logging.warning("%s is broken: %s", entry.path, e)
        except Exception:
            logging.exception("%s is broken", entry.path)
