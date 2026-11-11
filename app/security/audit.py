"""Administrative metadata only; private projects and credentials never belong here."""

import time

from app.security import accounts

CATEGORIES = {'all': 'All activity', 'team': 'Team', 'security': 'Security',
              'provider': 'AI provider', 'updates': 'Updates'}
ACTIONS = {
    'team.invite_created': 'Created invitation',
    'team.invite_renewed': 'Renewed invitation',
    'team.invite_revoked': 'Revoked invitation',
    'team.role_changed': 'Changed role',
    'team.username_changed': 'Changed username',
    'team.account_disabled': 'Disabled account',
    'team.account_enabled': 'Enabled account',
    'team.account_deleted': 'Deleted account',
    'security.policy_changed': 'Changed workspace two-factor policy',
    'security.two_factor_changed': 'Changed account two-factor settings',
    'provider.configuration_saved': 'Updated AI configuration',
    'updates.check_requested': 'Requested update check',
    'updates.install_requested': 'Requested update installation',
    'updates.cancel_requested': 'Requested update cancellation',
    'updates.credentials_changed': 'Updated registry credentials',
}
RESULTS = {'saved': 'Saved', 'requested': 'Requested', 'accepted': 'Accepted', 'failed': 'Failed'}
MANAGER_ACTIONS = {'team.invite_created', 'team.invite_renewed', 'updates.check_requested',
                   'updates.install_requested', 'updates.cancel_requested'}


def record(person: accounts.Identity, action: str, *, target: str = '', detail: str = '',
           result: str = 'saved', con=None) -> int:
    if action not in ACTIONS or result not in RESULTS:
        raise ValueError('Invalid administrative event.')
    if con is None:
        with accounts.database() as connection:
            connection.execute('BEGIN IMMEDIATE')
            return record(person, action, target=target, detail=detail, result=result, con=connection)
    actor = con.execute('''SELECT username,admin,
        id IN (SELECT user_id FROM invitation_managers) AS manager
        FROM users WHERE id=? AND disabled=0''', (person.id,)).fetchone()
    if actor is None or not (actor['admin'] or (actor['manager'] and action in MANAGER_ACTIONS)
                             or (action == 'team.username_changed' and target == actor['username'])):
        raise PermissionError('Administrator access required.')
    return con.execute('''INSERT INTO audit_events(occurred_at,actor_id,actor_name,action,target,detail,result)
        VALUES(?,?,?,?,?,?,?)''', (time.time(), person.id, actor['username'], action, target, detail, result)).lastrowid


def finish(event_id: int, result: str) -> None:
    if result not in {'accepted', 'failed', 'saved'}:
        raise ValueError('Invalid administrative result.')
    with accounts.database() as con:
        con.execute("UPDATE audit_events SET result=? WHERE id=? AND result='requested'", (result, event_id))


def entries(category: str = 'all', page: int = 1) -> list[dict]:
    if not accounts.identity().admin:
        raise PermissionError('Only Directors can view the activity log.')
    if category not in CATEGORIES or not 1 <= page <= 1_000_000:
        raise ValueError('Choose a valid category and page.')
    with accounts.database() as con:
        return [dict(row) for row in con.execute('''SELECT * FROM audit_events
            WHERE (?='all' OR action LIKE ?) ORDER BY id DESC LIMIT 51 OFFSET ?''',
            (category, category + '.%', (page - 1) * 50))]
