import json
import os

import requests

base_url = "http://localhost:8000/v1"

def test_login():
    email = "debug_user@example.com"
    password = os.getenv("DEBUG_AUTH_PASSWORD", "debug-password-placeholder")
    
    # Register
    print(f"Registering {email}...")
    reg_resp = requests.post(f"{base_url}/auth/register", json={
        "email": email,
        "password": password,
        "display_name": "Debug User"
    })
    print(f"Register Status: {reg_resp.status_code}")
    print(json.dumps(reg_resp.json(), indent=2))
    
    # Login
    print(f"Logging in {email}...")
    login_resp = requests.post(f"{base_url}/auth/login", json={
        "email": email,
        "password": password
    })
    print(f"Login Status: {login_resp.status_code}")
    payload = login_resp.json()
    print(json.dumps(payload, indent=2))
    
    return payload

if __name__ == "__main__":
    test_login()
