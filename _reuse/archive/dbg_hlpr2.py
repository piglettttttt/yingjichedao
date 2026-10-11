import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from hyperlpr3 import LicensePlateCatcher
import inspect
from hyperlpr3.inference.multitask_detect import MultiTaskDetectorORT
src = inspect.getsource(MultiTaskDetectorORT)
import re
# 找 pipeline 里返回的对象类型
from hyperlpr3 import LPRMultiTaskPipeline
print(inspect.getsource(LPRMultiTaskPipeline.__call__)[:1500])
