"""bedrock-strands-agent — form-extraction agent on Strands + Bedrock."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bedrock-strands-agent")
except PackageNotFoundError:  # pragma: no cover - happens in editable/dev installs
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
