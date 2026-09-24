import subprocess
import sys


if sys.platform == "win32":
    # The frontend tests pipe JS containing non-ASCII text through `subprocess.run(text=True)`,
    # which uses the ANSI codepage (cp1252) on Windows and raises UnicodeEncodeError. Node
    # always speaks UTF-8, so default text-mode pipes to UTF-8 here instead of editing every
    # upstream test file.
    _original_run = subprocess.run

    def _utf8_run(*args, **kwargs):
        if kwargs.get("text") and "encoding" not in kwargs:
            kwargs["encoding"] = "utf-8"
        return _original_run(*args, **kwargs)

    subprocess.run = _utf8_run
