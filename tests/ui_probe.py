"""UI 探针：单独渲染 SideDock 并打印各层 geometry，用于排查布局问题。

不连手机、不入库，纯造一张假卡片。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtWidgets import QApplication, QFrame  # noqa: E402

from dock import SideDock  # noqa: E402
from store import PendingItem  # noqa: E402
from thumbs import placeholder  # noqa: E402

app = QApplication(sys.argv)
dock = SideDock()

item = PendingItem(
    id="probe1",
    remote_path="/sdcard/DCIM/Screenshots/Screenshot_2026-10-30-01-03-21-968_com.tencent.mm.jpg",
    filename="Screenshot_2026-10-30-01-03-21-968_com.tencent.mm.jpg",
    staging="",
    mtime=1793293402,
    size=283086,
    status="pulled",
    created_at=0,
)
dock.add_item(item, placeholder("a.jpg"))

dock.show()
app.processEvents()

print(f"dock     {dock.geometry()}")
print(f"stack    {dock._stack.geometry()}")
for child in dock._stack.findChildren(QFrame):
    if child.objectName() == "panel":
        print(f"panel    {child.geometry()}  parent={child.parent().objectName() or type(child.parent()).__name__}")
print(f"scroll   {dock.scroll.geometry()}  visible={dock.scroll.isVisible()}")
print(f"dropzone {dock.drop_zone.geometry()}")
print(f"empty    {dock.empty_label.geometry()} visible={dock.empty_label.isVisible()}")
print(f"sizeHint dock={dock.sizeHint()} stack={dock._stack.sizeHint()}")

out = Path(r"C:\Users\sfl\AppData\Local\Temp\s2pc_dock.png")
print("grab:", dock.grab().save(str(out)))

# 收起态
dock.set_collapsed(True)
app.processEvents()
print(f"collapsed dock {dock.geometry()}")
out2 = Path(r"C:\Users\sfl\AppData\Local\Temp\s2pc_strip.png")
print("grab strip:", dock.grab().save(str(out2)))
