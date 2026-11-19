import json, uuid, urllib.request, urllib.error
BASE = "http://localhost:5000/api/v1"
FIELD = "file"          # <-- change if Step 2 showed a different name

def call(path, method="GET", body=None, token=None, raw=None, ctype=None):
    req = urllib.request.Request(BASE + path, method=method)
    if ctype: req.add_header("Content-Type", ctype)
    else: req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

def upload(path, filename, content):
    b = "----EduLedger" + uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{FIELD}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: text/csv\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    return call(path, "POST", raw=body, token=tok, ctype=f"multipart/form-data; boundary={b}")

s, r = call("/auth/login", "POST", {"email": "admin@gcu.edu.pk", "password": "admin"})
tok = r.get("token")
print("login:", s)

data = open("sample_directory.csv", "rb").read()
print("\n--- DRY RUN (writes nothing) ---")
s, dry = upload("/admin/directory/import?dry_run=true", "sample_directory.csv", data)
print(s, json.dumps(dry, indent=2, default=str)[:900])

print("\n--- REAL IMPORT ---")
s, real = upload("/admin/directory/import", "sample_directory.csv", data)
print(s, json.dumps(real, indent=2, default=str)[:900])

print("\n--- SECOND IMPORT (must create nothing) ---")
s, again = upload("/admin/directory/import", "sample_directory.csv", data)
print(s, json.dumps(again, indent=2, default=str)[:500])

print("\n--- DIRECTORY LIST ---")
s, lst = call("/admin/directory", "GET", None, tok)
print(s, json.dumps(lst, indent=2, default=str)[:1200])
