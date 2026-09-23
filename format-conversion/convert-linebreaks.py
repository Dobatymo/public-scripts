from argparse import ArgumentParser, ArgumentTypeError

from genutility.args import is_file, new_path
from genutility.filesystem import append_to_filename


def main() -> None:
    parser = ArgumentParser(description="Convert file to local linebreaks")
    parser.add_argument("infile", type=is_file)
    parser.add_argument("outfile", nargs="?", type=new_path)
    args = parser.parse_args()

    outfile = args.outfile or append_to_filename(args.infile, "converted")
    if args.outfile is None:
        try:
            new_path(outfile)
        except ArgumentTypeError as e:
            parser.error(str(e))

    with args.infile.open("r") as fr:
        with open(outfile, "x") as fw:
            fw.write(fr.read())


if __name__ == "__main__":
    main()
