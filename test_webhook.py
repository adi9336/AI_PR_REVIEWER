"""Test script to send mock GitHub webhook to the API."""

import json
import hmac
import hashlib
import os
import requests
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent / "backend" / ".env")

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "test-secret")
API_URL = "http://localhost:8000/webhook/github"


def generate_signature(payload: str, secret: str) -> str:
    """Generate HMAC SHA256 signature for GitHub webhook."""
    signature = hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return f"sha256={signature}"


def create_mock_webhook(
    repo: str = "test-owner/test-repo",
    pr_number: int = 42,
    action: str = "opened",
    delivery_uuid: str = None
) -> tuple[dict, dict]:
    """Create mock GitHub webhook payload and headers."""
    if delivery_uuid is None:
        delivery_uuid = str(uuid.uuid4())
    
    payload = {
        "action": action,
        "repository": {
            "name": repo.split("/")[-1],
            "full_name": repo
        },
        "pull_request": {
            "number": pr_number,
            "title": "Test PR for AI Review",
            "body": "This is a test PR to validate the AI review system",
            "head": {
                "sha": "abc123def456",
                "ref": "feature-branch",
                "label": "user:feature-branch"
            },
            "base": {
                "sha": "main123456",
                "ref": "main",
                "label": "repo:main"
            }
        }
    }
    
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_uuid,
        "Content-Type": "application/json"
    }
    
    return payload, headers


def send_webhook(payload: dict, headers: dict):
    """Send webhook to the API."""
    payload_str = json.dumps(payload)
    signature = generate_signature(payload_str, WEBHOOK_SECRET)
    headers["X-Hub-Signature-256"] = signature
    
    print(f"Sending webhook to {API_URL}")
    print(f"Delivery UUID: {headers['X-GitHub-Delivery']}")
    print(f"Action: {payload['action']}")
    print(f"Repo: {payload['repository']['full_name']}")
    print(f"PR: #{payload['pull_request']['number']}")
    
    try:
        response = requests.post(API_URL, data=payload_str, headers=headers)
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response: {response.json()}")
        return response
    except requests.exceptions.RequestException as e:
        print(f"Error sending webhook: {e}")
        return None


if __name__ == "__main__":
    
    # Check if server is running
    try:
        health_response = requests.get("http://localhost:8000/health")
        print(f"Server health check: {health_response.json()}")
    except requests.exceptions.RequestException:
        print("ERROR: Server not running. Start it with:")
        print("  ./.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --port 8000")
        exit(1)
    
    # Create and send webhook
    payload, headers = create_mock_webhook()
    response = send_webhook(payload, headers)
    
    if response and response.status_code == 202:
        print("\n✓ Webhook accepted successfully!")
        review_id = response.json().get("review_id")
        print(f"Review ID: {review_id}")
        print(f"\nYou can check the review at: http://localhost:8000/reviews/{review_id}")
