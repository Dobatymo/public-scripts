# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "genutility[sql]",
#     "pypyodbc",
# ]
# ///
from argparse import ArgumentParser

import pypyodbc
from genutility.sql import export_sql_to_csv


def do(connectstr: str, csvfile: str, query: str, quiet: bool) -> None:
    with pypyodbc.connect(connectstr) as conn:
        export_sql_to_csv(conn, csvfile, query, verbose=not quiet)


def main() -> None:
    parser = ArgumentParser(description="Import csv files to sqlite")
    parser.add_argument(
        "connectstr",
        help="Microsoft SQL Server ODBC driver connection string.\nExample: `Driver={SQL Server};Server=HOSTNAME;Database=DATABASENAME`",
    )
    parser.add_argument("csvfile", help="Path of the csv file. Supports .csv.gz and .csv.bz2 as well")
    parser.add_argument("query", help="SQL query.\nExample: `SELECT * FROM dbo.TABLENAME`")
    parser.add_argument("-q", "--quiet", action="store_true", help="Don't show progress")
    args = parser.parse_args()

    do(args.connectstr, args.csvfile, args.query, args.quiet)


if __name__ == "__main__":
    main()
