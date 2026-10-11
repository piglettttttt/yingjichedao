import sys, datetime, openpyxl, inspect
sys.path.insert(0, r"_reuse")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import check_recall

t = datetime.time(0,2)
print("literal type:", type(t), type(t) is datetime.time, isinstance(t, datetime.time))
print("func sees datetime module as:", check_recall.datetime, "is stdlib:", check_recall.datetime is datetime)

wb = openpyxl.load_workbook(r"downloads\目押记录.xlsx", data_only=True)
c = wb.worksheets[0].cell(row=1, column=1).value
print("\ncell value:", repr(c), "type:", type(c), "type() is datetime.time:", type(c) is datetime.time)
print("isinstance(c, datetime.time):", isinstance(c, datetime.time))
print("MRO:", type(c).__mro__)
print("has hour/min/sec:", hasattr(c,"hour"), hasattr(c,"minute"), hasattr(c,"second"))
print("c.hour,minute,second =", getattr(c,"hour",None), getattr(c,"minute",None), getattr(c,"second",None))
try:
    print("func(c) =", check_recall.mmss_to_sec(c))
except Exception as e:
    print("func(c) raised:", repr(e))
