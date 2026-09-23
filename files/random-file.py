from pathlib import Path

from genutility.args import new_path, non_negative_int
from genutility.rand import randbytes


def create_random_file(filename: Path, size: int, buffer_size: int = 1024) -> None:
    remaining = size
    with filename.open("xb") as fp:
        while remaining > 0:
            remaining -= buffer_size
            if remaining < 0:
                buffer_size += remaining
            fp.write(randbytes(buffer_size))


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("--path", default="random.bin", type=new_path)
    parser.add_argument("--size", default=1024**2, type=non_negative_int)
    args = parser.parse_args()

    create_random_file(args.path, args.size)
