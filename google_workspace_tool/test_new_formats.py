import sys
import logging
import os
from googleapiclient.discovery import build
from google_workspace_tool.auth_utils import get_credentials
from google_workspace_tool.drive_content_reader import read_drive_chunks
from google_workspace_tool.utils.url_parser import resolve_file_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

URLS = [
    ("docx", "https://docs.google.com/document/d/1K1XecyVkvQOg8GWBK5xjN_Jy88yLNeU9/edit?usp=sharing&ouid=116356573868357001882&rtpof=true&sd=true"),
    ("txt", "https://drive.google.com/file/d/1UCYm5kmDBJ-bctjebzdKQR63IzrFrSmM/view?usp=drive_link"),
    ("pptx_1", "https://docs.google.com/presentation/d/17qrwrytVanT21eZy-vXgDapf-41BvCZ5/edit?usp=drive_link&ouid=116356573868357001882&rtpof=true&sd=true"),
]

def test():
    creds = get_credentials(["https://www.googleapis.com/auth/drive.readonly"])
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    out_dir = "google_workspace_tool/test_results"
    os.makedirs(out_dir, exist_ok=True)
    
    for tag, url in URLS:
        try:
            file_id = resolve_file_id(url=url)
            meta = drive_service.files().get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True).execute()
            mime_type = meta.get("mimeType")
            name = meta.get("name")
            logger.info(f"Processing [{tag}] {name} ({mime_type}) - ID: {file_id}")
            
            chunks = read_drive_chunks(
                mime_type=mime_type,
                file_id=file_id,
                creds=creds,
                supports_all_drives=True,
                download_timeout=60,
            )
            
            out_file = os.path.join(out_dir, f"test_result_{tag}.md")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(f"# Source: {name}\n")
                f.write(f"**MIME**: {mime_type}\n")
                f.write(f"**Chunks**: {len(chunks)}\n\n")
                for c in chunks:
                    f.write(f"## Chunk: {c.chunk_id}\n")
                    f.write(f"**Heading**: {c.heading_path}\n")
                    f.write(f"**Has Table**: {c.has_table}\n")
                    f.write(f"**Tokens**: {c.token_count}\n\n")
                    f.write("```markdown\n")
                    f.write(c.content)
                    f.write("\n```\n\n---\n")
            
            logger.info(f"Saved {len(chunks)} chunks to {out_file}")
        except Exception as e:
            logger.error(f"Failed to process {tag}: {e}")

if __name__ == '__main__':
    test()
