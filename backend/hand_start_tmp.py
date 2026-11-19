import json, urllib.request, urllib.error
BASE = "http://localhost:5000/api/v1"
def call(path, method="GET", body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
print(call("/auth/register/student/start", "POST", {"roll_number": "CS-2023-001"}))
