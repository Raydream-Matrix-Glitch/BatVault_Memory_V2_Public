import sys, pathlib
root = pathlib.Path(__file__).parent
for pkg in root.iterdir():
    src = pkg / "src"
    if src.is_dir():
        sys.path.append(str(src))