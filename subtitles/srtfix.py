import logging
from typing import Callable

from genutility.exceptions import MalformedFile
from genutility.fileformats.srt import DEFAULT_ENCODING, transform
from genutility.func import identity


def sync_srt(
    infile,
    outfile,
    encoding,
    modify_times: Callable = identity,
    arg_times=(),
    modify_text: Callable = identity,
    arg_text=(),
):
    def sync(subtitle):
        subtitle.start, subtitle.end = modify_times(subtitle.start, *arg_times), modify_times(subtitle.end, *arg_times)
        subtitle.lines = modify_text(subtitle.lines, *arg_text)

    return transform(infile, outfile, sync, encoding)


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import existing_path, new_path
    from genutility.filesystem import scandir_rec
    from genutility.stdio import errorquit

    parser = ArgumentParser(description="Apply simple transformations to srt subtitles files.")
    parser.add_argument("inpath", type=existing_path, help="Input file or directory.")
    parser.add_argument("outpath", type=new_path, help="Output file or directory.")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="File encoding")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--fps", metavar=("FROM-FPS", "TO-FPS"), nargs=2, type=float, help="Change the rate from x fps to y fps"
    )
    group.add_argument("--delay", type=float, help="Delay by +x or -x seconds")

    args = parser.parse_args()

    if args.inpath.is_file() == args.outpath.is_file():
        errorquit("inpath and outpath must be both files or both directories")

    if args.inpath == args.outpath:
        errorquit("inpath is not allowed to equal outpath")

    if args.fps:

        def func(x):
            return x * args.fps[0] / args.fps[1]

    elif args.delay:

        def func(x):
            return x + args.delay

    if args.inpath.is_dir():
        for infile in scandir_rec(args.inpath, dirs=False, relative=True):
            outfile = args.outpath / infile.relpath
            print("In file: " + infile)
            print("Out file: " + outfile)
            try:
                sync_srt(infile.path, outfile, args.encoding, func)
            except MalformedFile as e:
                logging.error("File is malformed: %s", e)
    else:
        print("In file: " + args.inpath)
        print("Out file: " + args.outpath)
        sync_srt(args.inpath, args.outpath, args.encoding, func)
