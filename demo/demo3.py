#!/usr/bin/pyzy

import sys

if sys.version_info.major == 3:
  print('OK')
else:
  sys.exit('python interpreter version mismatch')
