import sys, openpyxl
sys.path.insert(0, r"_reuse")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from check_recall import mmss_to_sec
for v in ["00:02:00","00:11:00","0:02:00", "00:02"]:
    print(repr(v), "->", mmss_to_sec(v))
print("--- xlsx 原始单元格类型 ---")
wb = openpyxl.load_workbook(r"downloads\目押记录.xlsx", data_only=True)
ws = wb.worksheets[0]
for row in ws.iter_rows(values_only=True):
    print([ (c, type(c).__name__) for c in row ])
