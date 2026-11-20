"""归档根目录兜底回归：不能写死盘符，路径不可用要能自动回退。

    ./.venv/Scripts/python.exe tests/archive_root_test.py

发布给别人时对方电脑不一定有 D 盘；配置里残留测试路径（%TEMP%）也会丢文件。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from config import CONFIG, DEFAULTS  # noqa: E402
from main import App  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {label}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_root_"))
    # 注意：不能建在 %TEMP% 下，否则会先命中「临时目录」规则（这正是被测逻辑之一）
    normal = Path.home() / "__s2pc_guard_test__"
    blocker = Path.home() / "__s2pc_guard_file__.txt"
    default = str(DEFAULTS["archive_root"])
    try:
        check("默认归档根不再是写死的 D 盘", not default.lower().startswith("d:"), default)

        # --- 1. 正常路径：保留，并把目录建出来
        shutil.rmtree(normal, ignore_errors=True)
        CONFIG.set("archive_root", str(normal))
        App._guard_archive_root()
        check("正常路径不被改动", CONFIG.get("archive_root") == str(normal),
              str(CONFIG.get("archive_root")))
        check("目录已创建", normal.is_dir())

        # --- 2. 指向 %TEMP%：回退（避免文件随系统清理丢失）
        CONFIG.set("archive_root", str(Path(tempfile.gettempdir()) / "s2pc_tmp_archive"))
        App._guard_archive_root()
        check("临时目录会回退", CONFIG.get("archive_root") == default,
              str(CONFIG.get("archive_root")))

        # --- 3. 盘符不存在（模拟别人电脑没 D 盘）：回退而不是保存失败
        if Path("Q:/").exists():
            print("[SKIP] Q 盘存在，跳过「盘不存在」用例")
        else:
            CONFIG.set("archive_root", r"Q:\__s2pc_no_drive__\归档")
            App._guard_archive_root()
            check("盘不存在时回退", CONFIG.get("archive_root") == default,
                  str(CONFIG.get("archive_root")))

        # --- 4. 指向一个「文件」而不是目录：也要回退
        blocker.write_text("x", encoding="utf-8")
        CONFIG.set("archive_root", str(blocker))
        App._guard_archive_root()
        check("路径是文件时回退", CONFIG.get("archive_root") == default,
              str(CONFIG.get("archive_root")))
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(normal, ignore_errors=True)
        blocker.unlink(missing_ok=True)

    print()
    if FAIL:
        print(f"失败 {FAIL} 项，通过 {PASS} 项")
        return 1
    print(f"全部通过（{PASS} 项）")
    return 0


if __name__ == "__main__":
    sys.stdout.flush()
    code = main()
    sys.stdout.flush()
    os._exit(code)
