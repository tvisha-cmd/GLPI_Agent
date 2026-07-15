import os, requests
from dotenv import load_dotenv
load_dotenv()

r = requests.get(
    "https://generativelanguage.googleapis.com/v1beta/models",
    params={"key": os.environ["XAI_API_KEY"]},
)
data = r.json()
if "models" not in data:
    print("Error from Google:", data)
else:
    for m in data["models"]:
        if "generateContent" in m.get("supportedGenerationMethods", []):
            print(m["name"].removeprefix("models/"))