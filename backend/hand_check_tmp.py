import json, urllib.request
req = urllib.request.Request("http://localhost:5000/api/v1/auth/login", method="POST")
req.add_header("Content-Type", "application/json")
import urllib.request as u
r = u.urlopen(req, json.dumps({"email":"admin@gcu.edu.pk","password":"admin"}).encode())
tok = json.loads(r.read())["token"]
q = urllib.request.Request("http://localhost:5000/api/v1/admin/directory?search=BAD-001")
q.add_header("Authorization", "Bearer " + tok)
print("BAD-001 search:", json.loads(urllib.request.urlopen(q).read()))
