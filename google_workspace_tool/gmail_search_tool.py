"""
Gmail API v1 搜索封装工具。

提供薄封装层，用户直接传入 Gmail 原生 q 查询字符串，
工具负责分页、错误处理和结构化输出。

认证说明：
本模块不自行处理认证，必须由调用方传入已授权的 Credentials 对象。
请在外部统一完成 OAuth2 授权流程后，将 creds 传入本模块的各函数。

Gmail `messages().list()` q 参数支持的查询语法：
- from:email          发件人
- to:email            收件人
- cc: / bcc:email     抄送/密送
- subject:text        主题包含
- in:inbox/spam/trash/sent/draft/anywhere   位置
- label:xxx           标签过滤
- has:attachment      有附件
- filename:xxx        附件文件名/类型
- after:YYYY/MM/DD    晚于某日期
- before:YYYY/MM/DD   早于某日期
- older_than:1d/2m/1y 早于相对时间
- newer_than:1d/2m/1y 晚于相对时间
- is:important/starred/unread/read/draft/sent/snoozed/muted   状态
- category:primary/social/promotions/updates/forums   分类
- larger:5M / smaller:1M   大小
- has:drive/document/spreadsheet/presentation/youtube   内容类型
- list:email          邮件列表
- "exact phrase"      精确短语
- +word / -word       必须包含/排除
- OR / AND            逻辑组合
- ( )                 分组

常用 labelIds：
INBOX, UNREAD, SENT, DRAFT, SPAM, TRASH, STARRED, IMPORTANT,
CATEGORY_PERSONAL, CATEGORY_SOCIAL, CATEGORY_PROMOTIONS,
CATEGORY_UPDATES, CATEGORY_FORUMS

注意：messages().list() 只返回 id + threadId，不返回主题/正文/snippet。
如需详情需二次调用 messages().get()。
"""

from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

def search_gmail(
    q: Optional[str] = None,
    label_ids: Optional[list[str]] = None,
    max_results: int = 100,
    include_spam_trash: bool = False,
    max_pages: Optional[int] = None,
    creds=None,
    fetch_snippets: bool = False,
) -> list[dict]:
    """
    搜索 Gmail 邮件，返回消息 ID 列表。

    Args:
        q: Gmail q 查询字符串，例如：
           "from:boss@company.com after:2024/01/01 has:attachment"
           "subject:预算 is:unread"
           "\"project budget\" -draft"
           传 None 时返回所有邮件（按 max_results 分页）。
        label_ids: 标签过滤，例如 ["INBOX", "UNREAD"]
        max_results: 每页最大结果数，默认 100，最大 500
        include_spam_trash: 是否包含垃圾邮件和已删除邮件
        max_pages: 最大拉取页数，None 表示拉取全部
        creds: 外部传入的已授权 Credentials 对象，必填。
               请在外部统一完成 OAuth2 授权后传入。

    Returns:
        消息列表，每个元素为 dict：
        {"id": "...", "threadId": "..."}
        扁平化返回，所有页的结果合并为一个列表。

    Raises:
        ValueError: 当未传入 creds 时
        HttpError: 当 Google API 返回错误时（如 400 查询语法错误、403 权限不足）
    """
    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    messages = []
    page_token = None
    page_count = 0

    try:
        while True:
            params = {
                "userId": "me",
                "maxResults": min(max_results, 500),
                "pageToken": page_token,
                "includeSpamTrash": include_spam_trash,
            }
            if q is not None:
                params["q"] = q
            if label_ids is not None:
                params["labelIds"] = label_ids

            response = service.users().messages().list(**params).execute()

            batch = response.get("messages", [])
            messages.extend(batch)

            page_token = response.get("nextPageToken")
            page_count += 1

            if max_pages is not None and page_count >= max_pages:
                break
            if page_token is None:
                break

    except HttpError as e:
        raise HttpError(
            resp=e.resp,
            content=e.content,
            uri=e.uri,
        ) from e

    # 可选：顺序拉 snippet/subject（不能用 ThreadPool — googleapiclient service 非线程安全会卡死）
    # 5 messages × ~200ms = ~1s 顺序成本，可接受
    if fetch_snippets and messages:
        for m in messages:
            try:
                detail = service.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["Subject", "From", "Date"],
                ).execute()
                m["snippet"] = detail.get("snippet", "")
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                m["subject"] = headers.get("Subject", "")
                m["from"] = headers.get("From", "")
                m["date"] = headers.get("Date", "")
            except Exception:
                m["snippet"] = ""

    return messages


if __name__ == "__main__":
    # 本地快速测试：列出收件箱最近 10 封邮件
    result = search_gmail(
        q=None,
        label_ids=["INBOX"],
        max_results=10,
    )
    print(f"找到 {len(result)} 封邮件")
    for m in result:
        print(f"  messageId={m.get('id')} threadId={m.get('threadId')}")
