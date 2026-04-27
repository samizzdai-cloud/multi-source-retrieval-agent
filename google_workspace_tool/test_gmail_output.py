from auth_utils import get_credentials
from gmail_rag_pipeline import run_gmail_rag_pipeline
import os

token_path = os.path.join(os.path.dirname(__file__), "gmail_token.json")
creds = get_credentials(
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    token_path=token_path
)

if creds:
    output_fastlane = os.path.join(os.path.dirname(__file__), "test_results/gmail_rag_Fastlane.md")
    print(f"Testing with query: 'Fastlane'")
    run_gmail_rag_pipeline("Fastlane", creds, max_threads=2, output_file=output_fastlane)
    
    output_test = os.path.join(os.path.dirname(__file__), "test_results/gmail_rag_test.md")
    print(f"\nTesting with query: 'test'")
    run_gmail_rag_pipeline("test", creds, max_threads=2, output_file=output_test)
