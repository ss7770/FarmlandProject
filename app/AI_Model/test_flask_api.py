# -*- coding: utf-8 -*-
import sys, os, tempfile
sys.path.insert(0, r"D:\Twilight\FarmlandProject\project1.0\app\Python_Backend")
os.chdir(r"D:\Twilight\FarmlandProject\project1.0\app\Python_Backend")

import config
config.DATABASE_PATH = os.path.join(tempfile.gettempdir(), "test_disease.db")
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import app as flask_app
c = flask_app.app.test_client()

r1 = c.post("/api/disease/record", json={"disease": "番茄早疫病", "confidence": 0.92, "location": "大棚A"})
r2 = c.post("/api/disease/record", json={"disease": "x"})
r3 = c.get("/api/disease/records?limit=5")

out = []
out.append("POST valid  -> %s %s" % (r1.status_code, r1.get_json()))
out.append("POST invalid-> %s %s" % (r2.status_code, r2.get_json()))
out.append("GET list    -> %s %s" % (r3.status_code, r3.get_json()))

os.remove(config.DATABASE_PATH)

with open(r"D:\Twilight\FarmlandProject\project1.0\app\AI_Model\flask_test.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("done")
