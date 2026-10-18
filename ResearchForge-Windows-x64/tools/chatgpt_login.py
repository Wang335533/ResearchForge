r"""Run with runtime\python\python.exe -I -B tools\chatgpt_login.py."""
from pathlib import Path
import argparse
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run
run.configure(run.load_config())
import chatgpt_oauth


def main():
    parser = argparse.ArgumentParser(description='ResearchForge ChatGPT 套餐授权')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--test', action='store_true', help='一次简短 Responses 调用，使用套餐额度')
    parser.add_argument('--client-id', help='重新授权一个已有注册；默认新注册')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(chatgpt_oauth.status(), ensure_ascii=False, indent=2))
    elif args.models:
        print(json.dumps(chatgpt_oauth.list_models(), ensure_ascii=False, indent=2))
    elif args.test:
        text, usage = chatgpt_oauth.complete('Reply only OK.', 'Reply OK.', max_tokens=256)
        print(text)
        print(json.dumps(usage))
    else:
        current = chatgpt_oauth.status()
        chatgpt_oauth.start_login(args.client_id or current['active'])
        print('请在系统浏览器中完成 ChatGPT 登录与授权。', flush=True)
        while chatgpt_oauth.status()['pending']:
            time.sleep(1)
        result = chatgpt_oauth.status()
        print(result['message'])
        return 0 if result['connected'] else 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except chatgpt_oauth.OAuthError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
