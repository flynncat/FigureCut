"""Probe a candidate interpreter without third-party dependencies."""

import struct
import sys
import sysconfig


def main() -> int:
    if (sys.implementation.name != "cpython" or
            not (3, 11) <= sys.version_info[:2] <= (3, 13) or
            struct.calcsize("P") != 8 or sysconfig.get_config_var("Py_GIL_DISABLED")):
        return 1
    import ensurepip
    import tkinter
    import venv

    assert ensurepip and venv
    root = tkinter.Tk()
    root.withdraw()
    root.destroy()
    print("CUTOUT_PYTHON_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
