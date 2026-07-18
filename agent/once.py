"""Shorthand: python3 -m agent.once --label X --window 30"""
import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main(["once"] + sys.argv[1:]))
