import psutil
import time
import subprocess
import os

def test_io_counters():
    print("Starting download...")
    # Start a download in background
    p = subprocess.Popen(['wget', 'http://speedtest.tele2.net/10MB.zip', '-O', '/dev/null', '-q'])
    
    try:
        proc = psutil.Process(p.pid)
        print(f"Monitoring PID: {p.pid}")
        
        start_io = proc.io_counters()
        start_time = time.time()
        
        for i in range(5):
            time.sleep(1)
            try:
                curr_io = proc.io_counters()
                read_diff = curr_io.read_bytes - start_io.read_bytes
                write_diff = curr_io.write_bytes - start_io.write_bytes
                print(f"T+{i+1}s: Read={read_diff/1024:.2f}KB, Write={write_diff/1024:.2f}KB")
            except psutil.NoSuchProcess:
                break
                
    finally:
        p.terminate()
        p.wait()

if __name__ == "__main__":
    test_io_counters()
