import json
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from genutility.atomic import sopen
from genutility.hash import sha1_hash_file


class MicrosoftHashes:
    hashes: Dict[str, str]

    def __init__(self) -> None:
        try:
            with open("microsoft-hashes.json", encoding="utf-8") as fr:
                self.hashes = json.load(fr)
        except FileNotFoundError:
            self.hashes = {}

    def close(self) -> None:
        with sopen("microsoft-hashes.json", "wt", encoding="utf-8") as fw:
            json.dump(self.hashes, fw, ensure_ascii=False, indent="\t")

    @staticmethod
    def hash_to_page_url(hex: str, timeout: int = 30) -> Optional[str]:
        search_url = "https://www.heidoc.net/php/myvsdump_search.php"
        params = {
            "sha1": hex,
            "mindate": "1990-01-01T00:00",
            "maxdate": "2030-01-01T00:00",
        }

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

    @staticmethod
    def page_url_to_filename(page_url: str, timeout: int = 30) -> str:
        r = requests.get(page_url, timeout=timeout)
        r.raise_for_status()

        soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)
        tables = soup.find_all("table")
        assert len(tables) == 1
        td1, td2 = tables[0].tbody.tr.find_all("td")
        filename = td2.get_text()
        return filename

    def get_filename(self, hex: str) -> Optional[str]:
        try:
            filename = self.hashes[hex]
        except KeyError:
            url = self.hash_to_page_url(hex)
            if url:
                filename = self.page_url_to_filename(url)
            else:
                filename = None
            self.hashes[hex] = filename
        return filename

    def __enter__(self) -> "MicrosoftHashes":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def iter_hashes(basepath: Path) -> Iterator[Tuple[Path, str]]:
    for path in basepath.rglob("*.iso"):
        hex = sha1_hash_file(path).hexdigest()
        yield path, hex


def rename_all_in_folder(basepath: Path, move_to_subdir: bool) -> None:
    valid = "valid"

    with MicrosoftHashes() as hashes:
        for path, hex in iter_hashes(basepath):
            filename = hashes.get_filename(hex)
            if not filename:
                print(f"No filename found for <{path}> [{hex}]")
                continue

            if move_to_subdir and path.parent.name != valid:
                newpath = path.parent / valid / filename
            else:
                newpath = path.parent / filename

            if path == newpath:
                print(f"Already correct filename <{path}> [{hex}]")
                continue

            print(f"Renaming <{path}> to <{newpath}> [{hex}]")
            newpath.parent.mkdir(parents=True, exist_ok=True)
            path.rename(newpath)


if __name__ == "__main__":
    from argparse import ArgumentParser

    from genutility.args import is_dir

    parser = ArgumentParser()
    parser.add_argument("path", type=is_dir, default=Path("."))
    parser.add_argument("--move-to-subdir", action="store_true")
    args = parser.parse_args()

    rename_all_in_folder(args.path, args.move_to_subdir)
