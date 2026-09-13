# -*- coding: utf-8 -*-
"""
Fallback shims for optional dependencies that may be commented out of
requirements/prd.txt (see that file's header). Lets modules that only need
these for progress bars / background scheduling still import cleanly when
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

try:
    from unsync import unsync
except ImportError:

    def unsync(func):
        return func
