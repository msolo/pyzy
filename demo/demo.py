#!/usr/bin/pyzy
from __future__ import print_function
import sys

if sys.version_info.major == 2:
  print('OK')
else:
  sys.exit('python interpreter version mismatch')
