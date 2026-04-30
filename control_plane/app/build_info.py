"""Build identity for /v1/health responses. Replaced by CI at build time."""
import os

VERSION: str = os.environ.get("BUILD_GIT_SHA", "dev")
