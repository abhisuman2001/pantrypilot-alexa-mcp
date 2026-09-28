# conftest.py — project-level pytest configuration.
# Adds the project root to sys.path so that `import server` works
# regardless of where pytest is invoked from.
import sys
import os

# Insert the project root (the directory containing this file) at the
# front of sys.path.  This lets the tests do `import server` directly.
sys.path.insert(0, os.path.dirname(__file__))
