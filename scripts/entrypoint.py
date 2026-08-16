#!/usr/bin/env python3
"""Entry point for the packaged build. Not used when running from source.

PyInstaller runs its entry script as ``__main__`` with no package context, so
pointing it at ``ezvtt/__main__.py`` directly makes every ``from . import ...``
in that file fail with *"attempted relative import with no known parent
package"* -- and only in the built binary, never in development. This module
imports the package the normal way and hands over.

Kept as a separate two-line file rather than solved by rearranging
``ezvtt/__main__.py``, because ``python -m ezvtt`` is how the project is run
everywhere else and that should stay the primary path.
"""

from ezvtt.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
