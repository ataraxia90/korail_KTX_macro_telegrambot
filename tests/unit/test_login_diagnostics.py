import json
from types import SimpleNamespace
from threading import Thread
from unittest.mock import Mock

import pytest
import requests

from services.korail_service import KorailService
from utils.login_diagnostics import capture_login_responses, response_summary


def response(body, status=200, stage='login.Login'):
    result = requests.Response()
    result.status_code = status
    result.url = 'https://smart.letskorail.com/classes/com.korail.mobile.' + stage
    result.headers['Content-Type'] = 'application/json;charset=UTF-8'
    result._content = json.dumps(body).encode()
    return result


def test_missing_result_reports_rejection_without_sensitive_data():
    record = response_summary(response({
        'errorCode': '403', 'message': 'Access denied for secret-user secret-password',
        'Key': 'session-secret', 'strCustNm': 'private-name', 'secret-dynamic-key': 'private',
    }, 403), ('secret-user', 'secret-password'))
    assert record['http_status'] == 403
    assert record['has_strResult'] is False
    assert record['server_codes'] == ['403']
    assert record['message_categories'] == ['access_rejected']
    for secret in ('secret-user', 'secret-password', 'session-secret', 'private-name', 'secret-dynamic-key'):
        assert secret not in json.dumps(record)


def test_success_key_response_does_not_capture_encryption_material():
    record = response_summary(response({'strResult': 'SUCC', 'app.login.cphd': {
        'key': 'encryption-secret', 'idx': 'secret-index'}, 'h_msg_cd': 'API.I00000'}, stage='common.code.do'))
    assert record['stage'] == 'password_key'
    assert record['result'] == 'SUCC'
    assert 'encryption-secret' not in str(record)
    assert 'secret-index' not in str(record)


def test_credentials_cannot_be_reported_as_error_codes():
    record = response_summary(response({'code': 'secretpw', 'errorCode': '01012345678'}), ('secretpw',))
    assert record['server_codes'] == []


def test_non_json_response_reports_no_body():
    result = response({})
    result._content = b'<html>private-body</html>'
    record = response_summary(result)
    assert record['json_type'] == 'invalid'
    assert 'private-body' not in str(record)


def test_hook_is_thread_scoped_and_removed_on_error():
    client = SimpleNamespace(_session=requests.Session())
    existing = Mock()
    client._session.hooks['response'].append(existing)
    with pytest.raises(KeyError):
        with capture_login_responses(client) as records:
            hook = client._session.hooks['response'][-1]
            thread = Thread(target=hook, args=(response({}),))
            thread.start()
            thread.join()
            assert records == []
            hook(response({'strResult': 'SUCC'}, stage='common.code.do'))
            hook(response({'errorCode': '403'}))
            raise KeyError('strResult')
    assert [r['stage'] for r in records] == ['password_key', 'login']
    assert client._session.hooks['response'] == [existing]


def test_service_logs_diagnostics_without_credentials(monkeypatch, caplog):
    client = SimpleNamespace(_session=requests.Session())
    def login():
        for hook in client._session.hooks['response']:
            hook(response({'errorCode': '403'}))
        raise KeyError('strResult')
    client.login = login
    monkeypatch.setattr('services.korail_service.K2MKorail', lambda *a, **kw: client)
    service = KorailService()
    service._logged_in = True
    assert service.login('secret-user', 'secret-password') is False
    assert not service.is_logged_in
    assert 'response_metadata=' in caplog.text
    assert 'KeyError' in caplog.text
    assert 'secret-user' not in caplog.text
    assert 'secret-password' not in caplog.text
    assert client._session.hooks['response'] == []
