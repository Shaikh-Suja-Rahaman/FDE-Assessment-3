import requests
import os
key = os.environ.get("GEMINI_API_KEY", "")
url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro-latest:generateContent?key={key}"
r = requests.post(url, json={"contents": [{"parts": [{"text": "hi"}]}]})
print(r.status_code, r.text)
