import sys


def print_error(*objs):
    objs = [str(obj) for obj in objs]
    sys.stderr.write(", ".join(objs))
    sys.stderr.flush()
