from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("run3-mj-slimmer")
except PackageNotFoundError:
    __version__ = "unknown"
