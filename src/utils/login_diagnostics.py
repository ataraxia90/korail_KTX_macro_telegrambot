"""Observe login response metadata without logging bodies or credentials."""
from contextlib import contextmanager
import re
from threading import get_ident
from urllib.parse import urlsplit


def response_summary(response, secrets=()):
    stage = {
        '/classes/com.korail.mobile.common.code.do': 'password_key',
        '/classes/com.korail.mobile.login.Login': 'login',
    }.get(urlsplit(response.url).path)
    if stage is None:
        return None
    summary = {'stage': stage, 'http_status': response.status_code}
    content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
    summary['content_type'] = content_type if content_type in (
        'application/json', 'text/html', 'text/plain'
    ) else 'other'
    try:
        body = response.json()
    except ValueError:
        summary['json_type'] = 'invalid'
        return summary
    summary['json_type'] = type(body).__name__
    if not isinstance(body, dict):
        return summary
    summary['has_strResult'] = 'strResult' in body
    result = body.get('strResult')
    summary['result'] = result if result in ('SUCC', 'FAIL') else 'unknown'
    known = ('strResult', 'h_msg_cd', 'h_msg_txt', 'errorCode', 'errorMsg',
             'errorMessage', 'code', 'message', 'error', 'result', 'data', 'status')
    summary['known_fields'] = [key for key in known if key in body]
    summary['other_field_count'] = sum(key not in known for key in body)
    codes, categories = [], set()

    def inspect_fields(node, depth=0):
        for key, value in node.items():
            if key in ('h_msg_cd', 'errorCode', 'code', 'resultCode'):
                code = str(value)
                if code not in secrets and re.fullmatch(r'(?:[A-Za-z][A-Za-z0-9_.-]{0,19}|[0-9]{1,5})', code):
                    codes.append(code)
            if key in ('h_msg_txt', 'errorMsg', 'errorMessage', 'message', 'error', 'resultMsg') and isinstance(value, str):
                for category, words in {
                    'access_rejected': ('차단', '비정상', '접근 제한', 'access denied', 'forbidden', 'blocked'),
                    'rate_limited': ('too many', 'rate limit', '과도', '요청 횟수'),
                    'maintenance': ('점검', 'maintenance'),
                    'client_update': ('업데이트', '버전', 'update', 'version'),
                    'credentials_rejected': ('비밀번호 오류', '비밀번호가 일치하지', 'incorrect password', 'invalid password', '아이디 또는 비밀번호'),
                }.items():
                    if any(word in value.lower() for word in words):
                        categories.add(category)
            if isinstance(value, dict) and depth < 2:
                inspect_fields(value, depth + 1)

    inspect_fields(body)
    summary['server_codes'] = codes[:5]
    summary['message_categories'] = sorted(categories)
    return summary


@contextmanager
def capture_login_responses(client, secrets=()):
    """Filter shared-session hooks by thread and remove the hook after login."""
    records = []
    hooks = getattr(getattr(client, '_session', None), 'hooks', None)
    if not isinstance(hooks, dict) or not isinstance(hooks.get('response'), list):
        yield records
        return
    thread_id = get_ident()

    def observe(response, **kwargs):
        if get_ident() != thread_id:
            return
        try:
            record = response_summary(response, secrets)
            if record is not None:
                records.append(record)
                del records[:-4]
        except Exception:
            records.append({'diagnostic': 'unavailable'})

    response_hooks = hooks['response']
    response_hooks.append(observe)
    try:
        yield records
    finally:
        response_hooks.remove(observe)
