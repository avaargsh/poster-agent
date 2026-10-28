"""Local browser executables and disposable runtime paths for opt-in smoke tests."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

MAC_CHROME = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


def chromium_launch_options() -> dict[str, str]:
    executable = shutil.which('google-chrome') or shutil.which('google-chrome-stable')
    path = Path(executable) if executable else MAC_CHROME
    # Chrome loads frameworks relative to its executable inside the app bundle.
    # Launching a PATH symlink without resolving it breaks that lookup on macOS.
    return {'executable_path': str(path.resolve())} if path.is_file() else {}


def browser_test_environment(directory: str | Path) -> dict[str, str]:
    root = Path(directory).resolve()
    return {**os.environ, 'POSTER_DATA_DIR': str(root / 'posters'),
            'POSTER_EXECUTION_DB': str(root / 'executions.sqlite3'),
            'POSTER_CHECKPOINT_DB': str(root / 'checkpoints.sqlite3')}
