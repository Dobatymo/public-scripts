from __future__ import generator_stop

import json
import logging
from argparse import ArgumentParser

from bson import json_util
from genutility.iter import Progress
from genutility.json import json_lines
from genutility.signal import HandleKeyboardInterrupt
from pymongo import MongoClient

parser = ArgumentParser()
parser.add_argument("connection_string", metavar="connection-string", help="MongoDB connection URI")
parser.add_argument("--database", required=True, help="Database name")
parser.add_argument("--collection", required=True, help="Collection name")
parser.add_argument("--tls-ca-file", help="Specifies the location of a local .pem file that contains the root certificate chain from the Certificate Authority. This file is used to validate the certificate presented by the mongod/mongos instance.")
parser.add_argument("--tls-certificate-key-file", help="A file containing the client certificate and private key. If you want to pass the certificate and private key as separate files, use the ssl_certfile and ssl_keyfile options instead. Implies tls=True. Defaults to None.")
parser.add_argument("--tls-crl-file", help="A file containing a PEM or DER formatted certificate revocation list. Only supported by python 2.7.9+ (pypy 2.5.1+). Implies tls=True. Defaults to None.")
parser.add_argument("--out", help="JsonLines output file")

group = parser.add_mutually_exclusive_group(required=True)
group.add_argument("--find", type=json.loads, help="Query")
group.add_argument("--count", action="store_true", help="Count documents")

args = parser.parse_args()

client = MongoClient(args.connection_string, tlsCAFile=args.tls_ca_file, tlsCertificateKeyFile=args.tls_certificate_key_file, tlsCRLFile=args.tls_crl_file)

col = client[args.database][args.collection]

if args.count:
	print(col.count())
elif args.find is not None:

	if not args.out:
		parser.error("--out must be given for --find")

	with json_lines.from_path(args.out, "xt") as jl:
		Uninterrupted = HandleKeyboardInterrupt(True)
		for doc in Progress(col.find(args.find)):
			with Uninterrupted:
				try:
					jl.write(doc, default=json_util.default)
				except TypeError:
					logging.warning("Failed to serialize %s", doc)
					raise
