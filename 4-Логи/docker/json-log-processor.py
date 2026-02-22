#!/usr/bin/env python3
import json
import time
import re
import sys
from datetime import datetime

def parse_oncall_log(line):
    try:
       
        pattern = r'(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?P<logger>\w+) - (?P<level>\w+) - (?P<message>.*)'
        match = re.match(pattern, line.strip())
        
        if match:
            data = match.groupdict()
            
            log_time = datetime.strptime(data['timestamp'], '%Y-%m-%d %H:%M:%S,%f')
            timestamp = log_time.isoformat() + 'Z'
            
            structured_log = {
                "timestamp": timestamp,
                "level": data['level'].lower(),
                "logger": data['logger'],
                "message": data['message'],
                "service": "oncall",
                "environment": "production",
                "group": "ab5_statsenko"
            }
            
            return structured_log
        else:
            return {
                "timestamp": datetime.utcnow().isoformat() + 'Z',
                "level": "info",
                "message": line.strip(),
                "service": "oncall",
                "environment": "production", 
                "group": "ab5_statsenko"
            }
            
    except Exception as e:
        return {
            "timestamp": datetime.utcnow().isoformat() + 'Z',
            "level": "error",
            "message": f"Log parsing error: {str(e)} - Original line: {line}",
            "service": "oncall",
            "environment": "production",
            "group": "ab5_statsenko"
        }

def main():
    log_file = 'shared-logs/access.log'
    print("starting")
    try:
        print("trying opening file")
        with open(log_file, 'r') as f:
            print("open file")
        
            f.seek(0, 2)
            
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                
                structured_log = parse_oncall_log(line)
                
                print(json.dumps(structured_log), flush=True)
                
    except FileNotFoundError:
        print(json.dumps({
            "timestamp": datetime.utcnow().isoformat() + 'Z',
            "level": "error",
            "message": f"Log file not found: {log_file}",
            "service": "oncall",
            "environment": "production",
            "group": "ab5_statsenko"
        }), flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()