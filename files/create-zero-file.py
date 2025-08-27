from argparse import ArgumentParser
from pathlib import Path


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("size", type=int, help="File size in MB")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    data = b"\0" * 1024 * 1024

    with args.path.open("xb") as fw:
        for _i in range(args.size):
            fw.write(data)


if __name__ == "__main__":
    main()
