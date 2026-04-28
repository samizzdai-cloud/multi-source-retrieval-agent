"""Google Drive 文件下载工具。"""

import io
import time
from googleapiclient.http import MediaIoBaseDownload


def download_file(
    drive_service,
    file_id: str,
    supports_all_drives: bool = True,
    timeout: int = 15,
    chunk_size: int = 5 * 1024 * 1024,
) -> bytes:
    """通过 Drive API 下载文件的原始字节内容。

    Args:
        drive_service: drive(v3) service 对象
        file_id: Drive 文件 ID
        supports_all_drives: 是否支持共享驱动器
        timeout: 最长下载时间，超过主动阻断数据流
        chunk_size: 分块下载大小（默认 5MB），用于控制状态检测频率

    Returns:
        文件原始字节
    """
    request = drive_service.files().get_media(
        fileId=file_id, supportsAllDrives=supports_all_drives
    )
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request, chunksize=chunk_size)
    done = False
    start_time = time.time()
    
    while not done:
        if time.time() - start_time > timeout:
            raise TimeoutError(f"文件 {file_id} 下载超时（> {timeout}s），已主动切断流传输")
        _, done = downloader.next_chunk()
        
    return file_stream.getvalue()
