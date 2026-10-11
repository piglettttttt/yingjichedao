import sys
sys.path.insert(0, r"_reuse")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import check_recall, inspect
print(inspect.getsource(check_recall.mmss_to_sec))
import datetime
print("time(0,2) ->", check_recall.mmss_to_sec(datetime.time(0,2)))
