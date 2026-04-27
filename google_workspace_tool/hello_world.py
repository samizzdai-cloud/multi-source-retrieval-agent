import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# 权限范围：这里设置为“只读查看文件元数据”
# 如果需要读写文件内容，改为 'https://www.googleapis.com/auth/drive'
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']

def main():
    creds = None
    # token.json 存储用户的访问令牌，脚本会自动生成
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # 如果没有有效凭据，让用户登录
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 读取你刚才下载的 credentials.json
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # 保存凭据，下次运行就不用再登录了
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    # 建立服务连接
    service = build('drive', 'v3', credentials=creds)

    # 测试：列出最近的 10 个文件
    results = service.files().list(
        pageSize=10, fields="nextPageToken, files(id, name)").execute()
    items = results.get('files', [])

    if not items:
        print('没有找到任何文件。')
    else:
        print('最近的文件清单:')
        for item in items:
            print(f"{item['name']} ({item['id']})")

if __name__ == '__main__':
    main()