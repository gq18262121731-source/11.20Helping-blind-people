import asyncio
import base64
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import ocr
from backend.config import settings
from backend.main import app


def image_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (160, 80), 'white').save(buffer, format='JPEG')
    return buffer.getvalue()


def test_rapidocr_returns_boxes_in_reading_order():
    rows = [
        ([[80, 16], [150, 16], [150, 34], [80, 34]], '右侧文字', 0.95),
        ([[10, 12], [70, 12], [70, 30], [10, 30]], '第一行', 0.91),
        ([[12, 42], [68, 42], [68, 60], [12, 60]], '下一行', 0.88),
        ([[0, 0], [5, 0], [5, 5], [0, 5]], '低置信度', 0.1),
    ]
    text, boxes = ocr._rapid_text_and_boxes((rows, None))
    assert text == '第一行 右侧文字\n下一行'
    assert [box['text'] for box in boxes] == ['第一行', '右侧文字', '下一行']
    assert boxes[0]['points'] == [[10.0, 12.0], [70.0, 12.0], [70.0, 30.0], [10.0, 30.0]]


def test_ocr_sends_real_image_and_reports_truncation(monkeypatch):
    image = image_bytes()
    monkeypatch.setattr(settings, 'openai_base_url', 'http://model.test/v1')
    monkeypatch.setattr(settings, 'openai_model', 'vision-model')
    original_client = httpx.AsyncClient

    def handler(request):
        import json
        payload = json.loads(request.content)
        url = payload['messages'][1]['content'][1]['image_url']['url']
        assert base64.b64decode(url.split(',', 1)[1]) == image
        assert payload['messages'][0]['role'] == 'system'
        return httpx.Response(200, json={'choices': [{'message': {'content': '启明帽'}, 'finish_reason': 'length'}]})

    monkeypatch.setattr(ocr.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(ocr.recognize_text(image))
    assert result['text'] == '启明帽'
    assert result['has_text'] is True
    assert result['truncated'] is True
    assert result['boxes'] == []
    assert result['image_width'] == 160
    assert result['image_height'] == 80


@pytest.mark.parametrize('data', [b'', b'not an image', b'\xff\xd8broken'])
def test_invalid_images_rejected(data):
    with pytest.raises(ocr.OCRServiceError) as error:
        asyncio.run(ocr.recognize_text(data))
    assert error.value.status_code == 400


@pytest.mark.parametrize('failure, expected', [('timeout', 504), ('unreachable', 503), ('missing', 502), ('empty', 502), ('no_text', 200)])
def test_model_failures_and_no_text(monkeypatch, failure, expected):
    monkeypatch.setattr(settings, 'openai_base_url', 'http://model.test/v1')
    monkeypatch.setattr(settings, 'openai_model', 'vision-model')
    original_client = httpx.AsyncClient

    def handler(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('test', request=request)
        if failure == 'unreachable':
            raise httpx.ConnectError('test', request=request)
        if failure == 'missing':
            return httpx.Response(404)
        text = ocr.NO_TEXT + '。' if failure == 'no_text' else ''
        return httpx.Response(200, json={'choices': [{'message': {'content': text}}]})

    monkeypatch.setattr(ocr.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    if expected == 200:
        assert asyncio.run(ocr.recognize_text(image_bytes()))['has_text'] is False
    else:
        with pytest.raises(ocr.OCRServiceError) as error:
            asyncio.run(ocr.recognize_text(image_bytes()))
        assert error.value.status_code == expected


def test_busy_recognition_rejected(monkeypatch):
    monkeypatch.setattr(settings, 'openai_base_url', 'http://model.test/v1')
    monkeypatch.setattr(settings, 'openai_model', 'vision-model')

    async def check():
        async with ocr._recognition_lock:
            with pytest.raises(ocr.OCRServiceError) as error:
                await ocr.recognize_text(image_bytes())
            assert error.value.status_code == 429

    asyncio.run(check())


def test_endpoint_authentication_and_size_limit(tmp_path, monkeypatch):
    from backend import auth, database
    monkeypatch.setattr(settings, 'database_path', str(tmp_path / 'ocr.sqlite3'))
    database.init_db()
    auth.register_user('ocruser', 'ocr@example.com', 'Ocr123')
    token = auth.create_session('ocruser', 'Ocr123', False)['token']
    with TestClient(app, raise_server_exceptions=True) as client:
        assert client.post('/api/ocr/read', content=image_bytes()).status_code == 401
        headers = {'Authorization': 'Bearer ' + token}
        assert client.post('/api/ocr/read', content=b'bad', headers=headers).status_code == 400
        assert client.post('/api/ocr/read', content=b'x' * (ocr.MAX_IMAGE_BYTES + 1), headers=headers).status_code == 413
        from backend import main

        async def recognized(image):
            assert image == image_bytes()
            return {'type': 'ocr_result', 'text': '测试文字', 'has_text': True, 'truncated': False,
                    'model': 'test', 'boxes': [{'text': '测试文字', 'points': [[1, 2], [3, 2], [3, 4], [1, 4]]}],
                    'image_width': 160, 'image_height': 80}

        monkeypatch.setattr(main, 'recognize_text', recognized)
        response = client.post('/api/ocr/read', content=image_bytes(), headers=headers)
        assert response.status_code == 200
        assert response.json()['text'] == '测试文字'
        assert response.json()['boxes'][0]['text'] == '测试文字'
        assert response.json()['timestamp']
        assert client.get('/ocr-reader.js').status_code == 200
        assert client.get('/ocr-page.css').status_code == 200
        client.cookies.set('qh_token', token)
        dashboard = client.get('/dashboard.html').text
        assert 'QimingOCR.mount' in dashboard
        assert 'id="textReader"' in dashboard
        assert 'id="ocrCameraSelect"' in dashboard
