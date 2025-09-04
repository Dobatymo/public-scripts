# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "genutility[rich]",
#     "isbn-hyphenate",
#     "pyisbn",
#     "pypdf[crypto]>=6.0.0",
#     "regex",
#     "rich",
# ]
# ///

"""
issues:
- 9798350332865 cannot be hyphenated
"""

import logging
from itertools import islice
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

import isbn_hyphenate
import regex  # required for duplicate names in groups
from genutility.rich import Progress
from isbn_hyphenate.isbn_hyphenate import IsbnUnableToHyphenateError
from pyisbn import Isbn10, Isbn13
from pypdf import PdfReader
from pypdf.errors import EmptyFileError, FileNotDecryptedError, PdfStreamError
from rich import get_console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.progress import Progress as RichProgress


def isbn_to_13(isbn: str) -> str:
    isbn = isbn.replace("-", "")

    if len(isbn) == 10:
        isbn10 = Isbn10(isbn)
        isbn10.validate()
        isbn13 = isbn10.convert()
        return isbn_hyphenate.hyphenate(isbn13)
    elif len(isbn) == 13:
        isbn13 = Isbn13(isbn)
        isbn13.validate()
        return isbn_hyphenate.hyphenate(isbn)
    else:
        assert False


abbrv1 = ("isbn", "e-isbn", "isbn-13", "e-isbn-13", "doi", "obook isbn", "epdf isbn")
abbrv2 = ("ebk", "hbk", "cloth", "hb", "hardback", "hardcover", "ebook", "paperback", "pbk", "electronic")


def isbns_to_13_good(isbnss: Iterable[Sequence[str]], path: Any) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}

    type1: Optional[str]
    type2: Optional[str]

    for type1, isbn, type2 in isbnss:
        try:
            isbn = isbn_to_13(isbn)
        except IsbnUnableToHyphenateError:
            logging.error("Failed to hyphenate %s with %s", isbn, path)
            continue

        if type1 and type1.lower() not in abbrv1:
            logging.error("Unknown ISBN type1: %s", type1)
            type1 = None
        if type2 and type2.lower() not in abbrv2:
            logging.error("Unknown ISBN type2: %s", type2)
            type2 = None
        out[isbn] = type2 or type1 or None
    return out


def isbns_to_13(isbnss: Iterable[Sequence[str]], path: Any) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}

    for isbns in isbnss:
        isbns = [isbn for isbn in isbns if isbn]
        if len(isbns) == 1:
            try:
                isbn = isbn_to_13(isbns[0])
            except IsbnUnableToHyphenateError:
                logging.error("Failed to hyphenate %s with %s", isbns[0], path)
                continue
        else:
            raise ValueError(f"ISBNs contain to much info: {isbns}")
        out[isbn] = None

    return out


patterns = [
    r"\d{3}-\d{1}-\d{7}-\d{1}-\d",
    r"\d{3}-\d{1}-\d{5}-\d{3}-\d",
    r"\d{3}-\d{1}-\d{4}-\d{4}-\d",
    r"\d{3}-\d{1}-\d{3}-\d{5}-\d",
    r"\d{3}-\d{1}-\d{2}-\d{6}-\d",
    r"\d{3}-\d{2}-\d{3}-\d{4}-\d",
    r"\d-\d{5}-\d{3}-\d",
    r"\d-\d{4}-\d{4}-\d",
    r"\d-\d{2}-\d{6}-\d",
]

good_patterns = rf"(?i)(?:(?P<type1>{'|'.join(abbrv1)}):? )?(?:\d+\.\d+\/)?(?P<isbn>{'|'.join(patterns)})(?:\s+\((?P<type2>[a-zA-Z]{{2,16}})\))?|(?P<type1>ISBN|ISBN-13):? (?P<isbn>\d{{13}})(?:\s+(?P<type2>paperback|ebook))?"
bad_patterns1 = rf"doi\.org\/\d+\.\d+\/({'|'.join(patterns)})_\d+"
bad_patterns2 = rf"({'|'.join(patterns)})\/\d+"
bad_patterns = "(?i)" + bad_patterns1 + "|" + bad_patterns2


def extract(cp: regex.Pattern, cap: regex.Pattern, text: str, path: Any) -> Dict[str, Optional[str]]:
    content_isbns_anti = isbns_to_13(set(cap.findall(text)), path)
    content_isbns_page = isbns_to_13_good(set(cp.findall(text)), path)
    content_isbns_page = {k: v for k, v in content_isbns_page.items() if k not in content_isbns_anti}
    return content_isbns_page


def test() -> None:
    truth: Dict[str, Optional[str]]

    cp = regex.compile(good_patterns)
    cap = regex.compile(bad_patterns)

    text = "asd ISBN: 9781617292231 asd"
    truth = {"978-1-61729-223-1": "ISBN"}
    result = extract(cp, cap, text, "<unittest>")
    assert truth == result, (truth, result)

    text = "e-ISBN 978-1-61729-223-1 asd"
    truth = {"978-1-61729-223-1": "e-ISBN"}
    result = extract(cp, cap, text, "<unittest>")
    assert truth == result, (truth, result)

    text = "asd 978-1-61729-223-1/1 asd"
    truth = {}
    result = extract(cp, cap, text, "<unittest>")
    assert truth == result, (truth, result)


def find_isbn(
    path: Path, num_pages: Optional[int] = None, full_scan: bool = False, do: bool = False, rename_scene: bool = False
) -> None:
    cp = regex.compile(good_patterns)
    cap = regex.compile(bad_patterns)
    console = get_console()

    columns = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    with RichProgress(*columns, console=console) as p:
        progress = Progress(p)

        paths = list(progress.track(path.rglob("*.pdf"), description="Finding PDFs...", transient=True))

        for path in progress.track(paths, description="Reading PDFs...", transient=True):
            filename_isbns = set(cp.findall(path.name))
            filename_isbns13 = isbns_to_13_good(filename_isbns, path)
            content_isbns: Dict[str, Optional[str]] = {}

            if len(filename_isbns) == 0:
                _filename_isbn = None
                filename_isbn13 = None
            elif len(filename_isbns) == 1:
                _filename_isbn = filename_isbns.pop()
                filename_isbn13, _type = filename_isbns13.popitem()
            else:
                logging.error("More than one isbn in %s", path.name)
                continue

            try:
                reader = PdfReader(path)
            except (AttributeError, PdfStreamError, EmptyFileError) as e:
                logging.error("Failed to read %s (%s): %s", path, type(e).__name__, e)
                continue

            try:
                for page in islice(reader.pages, num_pages):
                    text = page.extract_text()

                    content_isbns_page = extract(cp, cap, text, path)
                    content_isbns.update(content_isbns_page)

                    if not full_scan and content_isbns:
                        break
            except FileNotDecryptedError as e:
                logging.error("Failed to read %s (%s): %s", path, type(e).__name__, e)
                continue

            if filename_isbn13 and content_isbns:
                if filename_isbn13 not in content_isbns:
                    logging.error("error %s: filename isbn %s not in content %s", path, filename_isbn13, content_isbns)
                else:
                    logging.debug("All good for %s", path)
            elif filename_isbn13 and not content_isbns:
                logging.debug("No content ISBN found for %s", path)
            elif not filename_isbn13 and content_isbns:
                if len(content_isbns) == 1:
                    content_isbn, _type = content_isbns.popitem()
                    new = path.with_name(f"{content_isbn} {path.name}")
                    if " " in path.name or rename_scene:
                        if do:
                            path.rename(new)
                        else:
                            console.print("rename", path.name, "to", new.name)
                    else:
                        logging.info("Skip scene %s", path)
                else:
                    logging.warning("Cannot rename %s: Multiple ISBNs found %s", path, content_isbns)
            else:
                logging.info("No ISBN found for %s", path)


def main() -> None:
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("--num-pages", type=int, default=20, help="Number of pages to search for ISBNs")
    parser.add_argument(
        "--do",
        action="store_true",
        help="By default the new filename are only printed, with this flag the files are actually renamed on disk",
    )
    parser.add_argument(
        "--rename-scene",
        action="store_true",
        help="By default, any PDFs which could be scene release (currently identifed by the lack of spaces in the filename) are skipped when renaming",
    )
    parser.add_argument(
        "--full-scan", action="store_true", help="If not specified, scan will stop after first page with ISBNs"
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("path", type=Path, help="Input directory use to search for PDF files")
    args = parser.parse_args()

    FORMAT = "%(message)s"
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
    else:
        logging.basicConfig(level=logging.WARNING, format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

    logging.getLogger("pypdf.generic._data_structures").setLevel(logging.ERROR)
    logging.getLogger("pypdf._reader").setLevel(logging.ERROR)
    logging.getLogger("pypdf._page").setLevel(logging.ERROR)

    find_isbn(args.path, args.num_pages, args.full_scan, args.do, args.rename_scene)


if __name__ == "__main__":
    test()
    main()
