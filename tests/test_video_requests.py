"""Director's request log exposes only identity and time, never private source material."""

import asyncio
import re
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.pipeline import jobs
from app.security import accounts as a
from app.web.main import app, enqueue
from test_security import people, headers, ORIGIN
from test_web import source_upload


@pytest.mark.parametrize('source', ['url', 'files'])
def test_only_accepted_requests_are_logged(people, monkeypatch, source):
    admin, member, *_ = people
    monkeypatch.setattr('app.web.main.provider.require', lambda: None)
    monkeypatch.setattr('app.web.main.UrlGuard.check', lambda self, url: url)
    payload = ({'data': {'url': 'https://private-customer.example', 'description': 'Secret campaign'}}
               if source == 'url' else {'json': {'files': [source_upload('private-brochure.png')]}})
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(member)
        def reject(_):
            raise HTTPException(429, 'Queue full')
        monkeypatch.setattr('app.web.main.enqueue', reject)
        assert web.post('/jobs', headers=credentials, **payload).status_code == 429
        with a.database() as con:
            assert con.execute('SELECT COUNT(*) FROM video_requests').fetchone()[0] == 0
        monkeypatch.setattr('app.web.main.enqueue', enqueue)
        monkeypatch.setattr(jobs, 'run_job', AsyncMock(return_value={}))
        before = time.time()
        response = web.post('/jobs', headers=credentials, follow_redirects=False, **payload)
        assert response.status_code == (303 if source == 'url' else 201)
        location = response.headers['location'] if source == 'url' else response.json()['location']
        with a.database() as con:
            rows = con.execute('SELECT * FROM video_requests').fetchall()
            assert len(rows) == 1
            assert set(rows[0].keys()) == {'id', 'user_id', 'requested_at'}
            assert rows[0]['user_id'] == member.id
            assert before <= rows[0]['requested_at'] <= time.time()
        page = web.get('/admin/video-requests', headers=headers(admin))
        assert page.status_code == 200 and 'alice' in page.text
        assert page.headers['cache-control'] == 'no-store'
        for secret in ('private-customer', 'Secret campaign', 'private-brochure', location):
            assert secret not in page.text
        assert page.text.count('<time datetime=') == 1
        assert 'Requested at · UTC' in page.text
        # Deleting the private video must not erase that a request was made.
        asyncio.run(jobs.delete_job(location.rsplit('/', 1)[1]))
        with a.database() as con:
            assert con.execute('SELECT COUNT(*) FROM video_requests').fetchone()[0] == 1


def test_request_log_access_pagination_and_account_lifecycle(people):
    admin, member, *_ = people
    # Existing jobs are not decrypted or backfilled into this log.
    jobs.create_job('https://old-private.example', 30)
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.get('/admin/video-requests', follow_redirects=False).status_code == 303
        credentials = headers(admin)
        assert 'No video requests yet.' in web.get('/admin/video-requests', headers=credentials).text
        with a.database() as con:
            con.executemany('INSERT INTO video_requests(user_id, requested_at) VALUES(?, ?)',
                            [(member.id, 1_700_000_000 + i) for i in range(51)])
        first = web.get('/admin/video-requests', headers=credentials)
        second = web.get('/admin/video-requests?page=2', headers=credentials)
        timestamps = re.findall(r'<time datetime="([^"]+)"', first.text + second.text)
        assert len(timestamps) == 51 and timestamps == sorted(timestamps, reverse=True)
        assert first.text.count('<time datetime=') == 50 and second.text.count('<time datetime=') == 1
        assert 'rel="next"' in first.text and 'rel="prev"' not in first.text
        assert 'rel="next"' not in second.text and 'rel="prev"' in second.text
        assert web.get('/admin/video-requests?page=0', headers=credentials).status_code == 422
        for role in ('user', 'manager'):
            a.set_role(admin, member.id, role)
            assert web.get('/admin/video-requests', headers=headers(member)).status_code == 403
            with pytest.raises(PermissionError):
                a.video_requests()
        with a.database() as con:
            con.execute('UPDATE users SET username=? WHERE id=?', ('renamed-user', member.id))
        renamed = web.get('/admin/video-requests', headers=credentials).text
        assert 'renamed-user' in renamed and '>alice<' not in renamed
        with a.database() as con:
            con.execute('UPDATE users SET disabled=1 WHERE id=?', (member.id,))
        a.delete_user(admin, member.id, 'renamed-user')
        with a.database() as con:
            assert con.execute('SELECT COUNT(*) FROM video_requests').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_request_log_is_atomic_with_queue_and_excludes_reviews(people, monkeypatch):
    run = AsyncMock(return_value={})
    resume = AsyncMock(return_value={})
    monkeypatch.setattr(jobs, 'run_job', run)
    monkeypatch.setattr(jobs, 'resume_job', resume)
    async with app.router.lifespan_context(app):
        jid = jobs.create_job('https://private.example', 30)
        with a.database() as con:
            con.execute("""CREATE TRIGGER reject_request BEFORE INSERT ON video_requests
                           BEGIN SELECT RAISE(ABORT, 'Synthetic storage failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            enqueue(jid)
        assert not app.state.tasks
        run.assert_not_called()
        with a.database() as con:
            assert con.execute('SELECT active FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 0
            con.execute('DROP TRIGGER reject_request')
        enqueue(jid)
        await asyncio.gather(*list(app.state.tasks.values()))
        enqueue(jid, {'action': 'approve'})
        await asyncio.gather(*list(app.state.tasks.values()))
        with a.database() as con:
            assert con.execute('SELECT COUNT(*) FROM video_requests').fetchone()[0] == 1
        run.assert_awaited_once()
        resume.assert_awaited_once()
