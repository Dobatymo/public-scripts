from genutility.xsl import xml_xslt_to_xhtml

if __name__ == "__main__":
	from argparse import ArgumentParser

	parser = ArgumentParser()
	parser.add_argument("xml", help="Input file")
	parser.add_argument("xslt", help="Input file")
	parser.add_argument("xhtml", help="Output file")
	args = parser.parse_args()

	xml_xslt_to_xhtml(args.xml, args.xslt, args.xhtml)
