# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[file,iter]",
# ]
# ///
from argparse import ArgumentParser
from pathlib import Path

from genutility.file import iterfilelike
from genutility.iter import iter_equal


def compare_content(a: Path, b: Path, size: int, a_seek: int = 0, b_seek: int = 0) -> bool:
    with a.open("rb") as fa, b.open("rb") as fb:
        fa.seek(a_seek)
        fb.seek(b_seek)
        return iter_equal(iterfilelike(fa, size), iterfilelike(fb, size))


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("file1", type=Path)
    parser.add_argument("file2", type=Path)
    parser.add_argument("seek1", type=int, default=0)
    parser.add_argument("seek2", type=int, default=0)
    parser.add_argument("maxsize", type=int)
    args = parser.parse_args()

    print(compare_content(args.file1, args.file2, args.maxsize, args.seek1, args.seek2))


if __name__ == "__main__":
    main()
