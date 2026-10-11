import sys, inspect
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from hyperlpr3 import LicensePlateCatcher
print("version:", getattr(__import__("hyperlpr3"), "__version__", "?"))
print(inspect.signature(LicensePlateCatcher.__init__))
print("--- __call__ ---")
print(inspect.signature(LicensePlateCatcher.__call__))
print("--- 源码片段 ---")
src = inspect.getsource(LicensePlateCatcher.__init__)
print(src[:2000])
