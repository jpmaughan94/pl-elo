"""Embed the data into the page as a fallback snapshot.
  python build_page.py <data dir> template.html pl-elo.html"""
import json, sys
from build_data import make

out = make(sys.argv[1])
html = open(sys.argv[2]).read().replace("__DATA__", json.dumps(out, separators=(",", ":")).replace("</", "<\\/"))
open(sys.argv[3], "w").write(html)
print("wrote", sys.argv[3], len(html) // 1024, "KB")
