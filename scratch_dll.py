import os
import psutil
import pyzbar.pyzbar

p = psutil.Process(os.getpid())
for dll in p.memory_maps():
    if "120" in dll.path.lower() or "zbar" in dll.path.lower() or "iconv" in dll.path.lower():
        print(dll.path)
