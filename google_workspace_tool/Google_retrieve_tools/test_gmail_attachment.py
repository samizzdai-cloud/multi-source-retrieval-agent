from auth_utils import get_credentials
from gmail_rag_pipeline import run_gmail_rag_pipeline
import os

token_path = os.path.join(os.path.dirname(__file__), "gmail_token.json")
creds = get_credentials(
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    token_path=token_path
)

if creds:
    result_chunks = run_gmail_rag_pipeline("has:attachment", creds, max_threads=1)
    if result_chunks:
        print(result_chunks[0])
