"""``python -m dupehunter`` 入口。"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in ("--gui", "-g", "gui"):
        from .cli import main as cli_main

        return cli_main(sys.argv[1:])

    from .gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
