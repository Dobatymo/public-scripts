from __future__ import generator_stop

import json
import logging
from argparse import ArgumentParser

from bson import json_util
from genutility.args import json_file
from genutility.iter import progress
from genutility.json import json_lines
from genutility.signal import HandleKeyboardInterrupt
from pymongo import MongoClient


def main(args):
    client = MongoClient(
        args.connection_string,
        tlsCAFile=args.tls_ca_file,
        tlsCertificateKeyFile=args.tls_certificate_key_file,
        tlsCRLFile=args.tls_crl_file,
    )

    col = client[args.database][args.collection]

    if args.count:
        print(col.count())
    elif args.find is not None or args.agg is not None:

        if not args.out:
            parser.error("--out must be given for --find or --agg")

        if args.find:
            cursor = col.find(args.find, no_cursor_timeout=True)
        elif args.agg:
            cursor = col.aggregate(args.agg)
        else:
            parser.error("--find or --agg must be specified")

        with cursor:
            with json_lines.from_path(args.out, "xt") as jl:
                Uninterrupted = HandleKeyboardInterrupt(True)
                cursor = cursor.batch_size(args.batch_size)
                for doc in progress(cursor):
                    with Uninterrupted:
                        try:
                            jl.write(doc, default=json_util.default)
                        except TypeError:
                            logging.warning("Failed to serialize %s", doc)
                            raise


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("connection_string", metavar="connection-string", help="MongoDB connection URI")
    parser.add_argument("--database", required=True, help="Database name")
    parser.add_argument("--collection", required=True, help="Collection name")
    parser.add_argument("--batch-size", type=int, default=10000, help="Batch size")
    parser.add_argument(
        "--tls-ca-file",
        help="Specifies the location of a local .pem file that contains the root certificate chain from the Certificate Authority. This file is used to validate the certificate presented by the mongod/mongos instance.",
    )
    parser.add_argument(
        "--tls-certificate-key-file",
        help="A file containing the client certificate and private key. If you want to pass the certificate and private key as separate files, use the ssl_certfile and ssl_keyfile options instead. Implies tls=True. Defaults to None.",
    )
    parser.add_argument(
        "--tls-crl-file",
        help="A file containing a PEM or DER formatted certificate revocation list. Only supported by python 2.7.9+ (pypy 2.5.1+). Implies tls=True. Defaults to None.",
    )
    parser.add_argument("--out", help="JsonLines output file")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--find", dest="find", type=json.loads, help="Find query")
    group.add_argument("--find-file", dest="find", type=json_file, help="Find query")
    group.add_argument("--agg", dest="agg", type=json.loads, help="Aggregate query")
    group.add_argument("--agg-file", dest="agg", type=json_file, help="Aggregate query")
    group.add_argument("--count", action="store_true", help="Count documents")

    args = parser.parse_args()

    main(args)
