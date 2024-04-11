import pypyodbc
from genutility.sql import export_sql_to_csv


def main(args):
    with pypyodbc.connect(args.connectstr) as conn:
        export_sql_to_csv(conn, args.csvfile, args.query, verbose=not args.quiet)


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Import csv files to sqlite")
    parser.add_argument(
        "connectstr",
        help="Microsoft SQL Server ODBC driver connection string.\nExample: `Driver={SQL Server};Server=HOSTNAME;Database=DATABASENAME`",
    )
    parser.add_argument("csvfile", help="Path of the csv file. Supports .csv.gz and .csv.bz2 as well")
    parser.add_argument("query", help="SQL query.\nExample: `SELECT * FROM dbo.TABLENAME`")
    parser.add_argument("-q", "--quiet", action="store_true", help="Don't show progress")
    args = parser.parse_args()

    main(args)
