# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "beautifulsoup4",
#     "genutility[args,atomic,hash]",
#     "requests",
#     "typing-extensions",
# ]
# ///
import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from genutility.args import is_dir
from genutility.atomic import sopen
from genutility.hash import sha1_hash_file
from typing_extensions import Self

DEFAULT_TIMEOUT = 30
DEFAULT_CACHEFILE = "microsoft-hashes.json"


class BackendBase:
    @classmethod
    def hash_to_page_url(cls, hashhex: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
        raise NotImplementedError

    @classmethod
    def page_url_to_filename(cls, page_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        raise NotImplementedError


class HeidocBackend(BackendBase):
    @classmethod
    def hash_to_page_url(cls, hashhex: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
        search_url = "https://www.heidoc.net/php/myvsdump_search.php"
        params = {"sha1": hashhex, "mindate": "1990-01-01T00:00", "maxdate": "2030-01-01T00:00"}
        r = requests.get(search_url, params=params, timeout=timeout)
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)
        tables = soup.find_all("table")

        if len(tables) > 1:
            raise RuntimeError("More than one file found for this hash")

        a = tables[0].tbody.tr.td.a
        if a is None:
            return None

        return a["href"]

    @classmethod
    def page_url_to_filename(cls, page_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        r = requests.get(page_url, timeout=timeout)
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)
        tables = soup.find_all("table")
        assert len(tables) == 1
        td1, td2 = tables[0].tbody.tr.find_all("td")
        filename = td2.get_text()
        return filename


class AdguardBackend(BackendBase):
    @classmethod
    def hash_to_page_url(cls, hashhex: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
        search_url = "https://files.rg-adguard.net/search"
        data = {"search": hashhex}
        r = requests.post(search_url, data=data, timeout=timeout)
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)
        tables = soup.find_all("table", class_="info")
        assert len(tables) == 1

        links = [td.a for td in tables[0].find_all("td", class_="desc")]

        if len(links) == 0:
            return None
        elif len(links) == 1:
            return links[0]["href"]
        else:
            logging.warning("URLs: %r", links)
            raise RuntimeError("More than one file found for this hash")

    @classmethod
    def page_url_to_filename(cls, page_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        r = requests.get(page_url, timeout=timeout)
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)
        tables = soup.find_all("table", class_="info")
        assert len(tables) == 1

        for tr in tables[0].find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) != 2:
                continue
            left = tds[0].get_text().strip()
            right = tds[1].get_text().strip()
            if left == "File:":
                return right

        msg = f"Filename not found on <{page_url}>"
        raise AssertionError(msg)


class MicrosoftHashes:
    hashes: Dict[str, Optional[str]]
    backendsmap = {"heidoc": HeidocBackend, "adguard": AdguardBackend}

    def __init__(self, cachefile: Optional[str] = None, backend: Optional[str] = None) -> None:
        self.cachefile = cachefile or DEFAULT_CACHEFILE

        if backend is None:
            self.backends = [b() for b in self.backendsmap.values()]
        else:
            self.backends = [self.backendsmap[backend]()]

        try:
            with open(self.cachefile, encoding="utf-8") as fr:
                self.hashes = json.load(fr)
        except FileNotFoundError:
            self.hashes = {}

    def close(self) -> None:
        with sopen(self.cachefile, "wt", encoding="utf-8") as fw:
            json.dump(self.hashes, fw, ensure_ascii=False, indent="\t")

    def get_filename(self, hashhex: str, retry: bool) -> Optional[str]:
        found = hashhex in self.hashes
        filename = self.hashes.get(hashhex)

        if not found or (filename is None and retry):
            for backend in self.backends:
                url = backend.hash_to_page_url(hashhex)
                logging.debug("[%s] URL: %s", type(backend).__name__, url)
                if url:
                    filename = backend.page_url_to_filename(url)
                    break
            else:
                filename = None
            self.hashes[hashhex] = filename
        return filename

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args) -> None:
        self.close()


def iter_hashes(basepath: Path, recursive: bool = False) -> Iterator[Tuple[Path, str]]:
    if recursive:
        globfunc = basepath.rglob
    else:
        globfunc = basepath.glob

    for path in globfunc("*.iso"):
        hashhex = sha1_hash_file(path).hexdigest()
        yield path, hashhex


def rename_all_in_folder(basepath: Path, do: bool, retry: bool, move_to_subdir: bool, recursive: bool = False) -> None:
    valid = "valid"

    with MicrosoftHashes() as hashes:
        for path, hashhex in iter_hashes(basepath, recursive):
            logging.debug("<%s> [%s]", path, hashhex)
            filename = hashes.get_filename(hashhex, retry)
            if not filename:
                print(f"No filename found for <{path}> [{hashhex}]")
                continue

            if move_to_subdir and path.parent.name != valid:
                newpath = path.parent / valid / filename
            else:
                newpath = path.parent / filename

            if path == newpath:
                print(f"Already correct filename <{path}> [{hashhex}]")
                continue

            print(f"Renaming <{path}> to <{newpath}> [{hashhex}]")
            if do:
                newpath.parent.mkdir(parents=True, exist_ok=True)
                path.rename(newpath)


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("path", type=is_dir, default=Path())
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("--move-to-subdir", action="store_true")
    parser.add_argument("--do", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--retry", action="store_true", help="Try again to find filename which were previously not found"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)

    rename_all_in_folder(args.path, args.do, args.retry, args.move_to_subdir, args.recursive)


if __name__ == "__main__":
    main()
