# -*- coding: utf-8 -*-
"""
Fallback shims for optional dependencies that live in
requirements/optional.txt rather than requirements/prd.txt, so they aren't
installed by default (see CLAUDE.md's optional-dependency pattern). Lets
modules that only need these for progress bars still import cleanly when
the real packages aren't installed - the features that actually need them
(AI analysis, screenshot capture) fail at call time instead, not at import
time, so the app still boots.
"""

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable, *args, **kwargs):
        return iterable


try:
    from tqdm.asyncio import tqdm as async_tqdm
except ImportError:
    async_tqdm = tqdm
