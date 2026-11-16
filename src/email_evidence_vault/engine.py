import hashlib

def calculate(rows, payload):
    parent={r["id"]:(r["refs"][-1] if r.get("refs") else r["id"]) for r in rows}; threads={}
    def root(mid):
        seen=set()
        while parent.get(mid,mid) not in seen and parent.get(mid,mid)!=mid: seen.add(mid); mid=parent[mid]
        return mid
    for row in rows: threads.setdefault(root(row["id"]),[]).append(row["id"])
    attachments={}
    for row in rows:
        for att in row.get("attachments",[]):
            digest=hashlib.sha256(att["content"].encode()).hexdigest(); attachments[digest]=att["name"]
    return {"threads":{k:sorted(v) for k,v in threads.items()},"attachment_count":len(attachments),"held_messages":sorted(set(payload.get("holds", [])) & {r["id"] for r in rows})}
