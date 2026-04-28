import sys
import logging
import asyncio
from tools.google.google_retrieve import google_retrieve

logging.basicConfig(level=logging.INFO)

def test():
    chunks = asyncio.run(google_retrieve("test", max_results=1, max_read_time=15))
    print(f"Retrieved {len(chunks)} chunks.")
    if chunks:
        print(f"Sample chunk: {chunks[0].content[:100]}...")

if __name__ == '__main__':
    test()
