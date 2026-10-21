#!/usr/bin/env python3
"""Create offline combined playback from an existing telephone evidence directory."""
import argparse
from pathlib import Path

from voice_harness.call_review import render_review, verify_review

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
parser.add_argument("--speech-cache", type=Path)
args = parser.parse_args()
result = render_review(args.directory, speech_cache=args.speech_cache)
print("Alignment:", result["alignment_mode"])
print("Playback:", (args.directory / "index.html").resolve())
print("Derived evidence verified:", verify_review(args.directory))
