"""Google Drive 文件下载工具。"""

import io
from googleapiclient.http import MediaIoBaseDownload


def download_file(drive_service, file_id: str, supports_all_drives: bool = True) -> bytes:
    """通过 Drive API 下载文件的原始字节内容。

    Args:
        drive_service: drive(v3) service 对象
        file_id: Drive 文件 ID
        supports_all_drives: 是否支持共享驱动器

    Returns:
        文件原始字节
    """
    request = drive_service.files().get_media(
        fileId=file_id, supportsAllDrives=supports_all_drives
    )
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return file_stream.getvalue()
