import json
import urllib.request
import ssl
import sys

# SSL context (skip verify for DingTalk)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

def send_dingtalk(title, content):
    """发送钉钉消息"""
    url = 'https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba'
    
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': content
        }
    }
    
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    # Truncate if too long for DingTalk (limit ~20000 chars)
    if len(content) > 18000:
        content = content[:17500] + '\n\n--- 内容过长已截断 ---'
        payload['markdown']['text'] = content
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('errcode') == 0:
                print('推送成功！')
                return True
            else:
                print(f'推送失败: {result}')
                return False
    except urllib.error.URLError as e:
        print(f'URL错误: {e.reason}')
        return False
    except TimeoutError:
        print(f'超时: 20s')
        return False
    except Exception as e:
        print(f'错误: {type(e).__name__}: {e}')
        return False

if __name__ == '__main__':
    if len(sys.argv) >= 3:
        title = sys.argv[1]
        content = sys.argv[2]
        send_dingtalk(title, content)
    else:
        print('用法: python dingtalk.py "标题" "内容"')
