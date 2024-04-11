from genutility.file import file_byte_reader
from genutility.math import discrete_distribution
from genutility.numpy import shannon_entropy


def file_entropy(filename):
    d, s = discrete_distribution(b[0] for b in file_byte_reader(filename, 1024, 1))
    p = {k: v / s for k, v in d.items()}
    return shannon_entropy(p.values(), 256)


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()

    print(file_entropy(args.path))
