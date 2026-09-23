# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[args,sql]>=0.0.123",
#     "pypyodbc",
# ]
# ///
from argparse import ArgumentParser
from os import fspath
from pathlib import Path

import pypyodbc
from genutility.args import is_file_or_new_nofollow
from genutility.sql import export_sql_to_csv


def do(connectstr: str, csvfile: Path, query: str, quiet: bool) -> None:
    with pypyodbc.connect(connectstr) as conn:
        export_sql_to_csv(conn, fspath(csvfile), query, verbose=not quiet)


def main() -> None:
    parser = ArgumentParser(description="Import csv files to sqlite")
    parser.add_argument(
        "connectstr",
        help="Microsoft SQL Server ODBC driver connection string.\nExample: `Driver={SQL Server};Server=HOSTNAME;Database=DATABASENAME`",
    )
    parser.add_argument(
        "csvfile", type=is_file_or_new_nofollow, help="Path of the csv file. Supports .csv.gz and .csv.bz2 as well"
    )
    parser.add_argument("query", help="SQL query.\nExample: `SELECT * FROM dbo.TABLENAME`")
    parser.add_argument("-q", "--quiet", action="store_true", help="Don't show progress")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite the output CSV")
    args = parser.parse_args()

    output_exists = args.csvfile.exists()
    if output_exists and not args.overwrite:
        parser.error(f"{args.csvfile} already exists; use --overwrite to replace it")

    do(args.connectstr, args.csvfile, args.query, args.quiet)


if __name__ == "__main__":
    main()
