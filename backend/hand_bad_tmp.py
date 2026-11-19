import json, uuid, urllib.request, urllib.error
BASE = "http://localhost:5000/api/v1"
def call(path, method="GET", body=None, token=None, raw=None, ctype=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", ctype if ctype else "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
s, r = call("/auth/login", "POST", {"email": "admin@gcu.edu.pk", "password": "admin"})
tok = r.get("token")
b = "----EduLedger" + uuid.uuid4().hex
body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"bad_directory.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\n").encode() + open("bad_directory.csv","rb").read() + f"\r\n--{b}--\r\n".encode()
s, out = call("/admin/directory/import", "POST", raw=body, token=tok, ctype=f"multipart/form-data; boundary={b}")
print("status:", s)
print(json.dumps(out, indent=2, default=str))
