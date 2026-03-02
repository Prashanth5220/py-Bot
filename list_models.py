import requests
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("GOOGLE_API_KEY")

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
response = requests.get(url)
if response.status_code == 200:
    for m in response.json().get('models', []):
        print(f"{m['name']} - {m.get('supportedGenerationMethods', [])}")
else:
    print(response.text)
