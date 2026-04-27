from auth_utils import get_credentials
from gmail_rag_pipeline import run_gmail_rag_pipeline
import os

token_path = os.path.join(os.path.dirname(__file__), "gmail_token.json")
creds = get_credentials(
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    token_path=token_path
)

if creds:
    print("Testing with query: 'Fastlane'")
    result_chunks = run_gmail_rag_pipeline("Fastlane", creds, max_threads=2)
    for i, chunk in enumerate(result_chunks):
        print(f"\n========== Chunk {i+1} ==========\n")
        print(chunk)

    print("\nTesting with query: 'test'")
    result_chunks = run_gmail_rag_pipeline("test", creds, max_threads=2)
    for i, chunk in enumerate(result_chunks):
        print(f"\n========== Chunk {i+1} ==========\n")
        print(chunk)
