"""The privileged adapter: only containers belonging to this installation are replaceable."""

import base64
import copy
import json
from urllib.parse import quote

import httpx

LABEL = 'org.kiehnema.installation'


class Docker:
    def __init__(self, installation, socket='/var/run/docker.sock'):
        self.installation = installation
        self.client = httpx.Client(transport=httpx.HTTPTransport(uds=socket), base_url='http://docker/v1.44', timeout=90)

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def name(self, role):
        if role not in {'app', 'updater', 'gateway', 'runner', 'migration'}:
            raise ValueError('Invalid installation component.')
        return f'{self.installation}-{role}'

    def inspect(self, role):
        try:
            value = self.request('GET', f'/containers/{self.name(role)}/json')
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if value['Config'].get('Labels', {}).get(LABEL) != self.installation:
            raise ValueError('Container does not belong to this installation.')
        return value

    def snapshot(self, role):
        container = self.inspect(role)
        if container is None:
            raise ValueError(f'Missing installation component: {role}.')
        config = {key: container['Config'][key] for key in (
            'User', 'Env', 'Cmd', 'Entrypoint', 'WorkingDir', 'Labels', 'ExposedPorts', 'StopSignal', 'Healthcheck'
        ) if container['Config'].get(key) is not None}
        # Image defaults (PATH, Python/browser versions) must come from the replacement image.
        config['Env'] = [entry for entry in config.get('Env', []) if entry.startswith(('MG_', 'KIEHNEMA_', 'AWS_'))]
        config['HostConfig'] = copy.deepcopy(container['HostConfig'])
        # Preserve the allocated port too when the original request used a dynamic host port.
        config['HostConfig']['PortBindings'] = {port: bindings for port, bindings in
            container['NetworkSettings'].get('Ports', {}).items() if bindings}
        config['NetworkingConfig'] = {'EndpointsConfig': {
            name: {'Aliases': [alias for alias in endpoint.get('Aliases') or [] if alias != container['Id'][:12]]}
            for name, endpoint in container['NetworkSettings']['Networks'].items()
        }}
        config['Image'] = container['Config']['Image']
        return config

    def stop(self, role):
        container = self.inspect(role)
        if container and container['State']['Running']:
            self.request('POST', f'/containers/{self.name(role)}/stop?t=60')

    def remove(self, role):
        container = self.inspect(role)
        if container:
            if container['State']['Running']:
                raise ValueError('Refusing to remove a running container.')
            self.request('DELETE', f'/containers/{self.name(role)}')

    def start(self, role):
        container = self.inspect(role)
        if container and not container['State']['Running']:
            self.request('POST', f'/containers/{self.name(role)}/start')

    def replace(self, role, image, specification):
        self.stop(role)
        self.remove(role)
        spec = copy.deepcopy(specification)
        spec['Image'] = image
        spec.setdefault('Labels', {})[LABEL] = self.installation
        path = '/containers/create'
        # Volume subdirectories require API 1.45; bind mounts still work with Engine 25.
        if any(mount.get('VolumeOptions', {}).get('Subpath') for mount in spec.get('HostConfig', {}).get('Mounts', [])):
            path = 'http://docker/v1.45/containers/create'
        self.request('POST', path, params={'name': self.name(role)}, json=spec)
        self.start(role)

    def run_helper(self, role, image, specification):
        existing = self.inspect(role)
        if existing and existing['State']['Running']:
            return
        spec = copy.deepcopy(specification)
        spec.update(Cmd=['python', '-m', 'app.updates', role], Entrypoint=None, User='0:0', WorkingDir='/opt/kiehnema')
        spec.pop('ExposedPorts', None)
        spec['HostConfig'].update(RestartPolicy={'Name': 'no'}, PortBindings={})
        spec['NetworkingConfig'] = {'EndpointsConfig': {name: {} for name in spec['NetworkingConfig']['EndpointsConfig']}}
        self.replace(role, image, spec)

    def pull(self, ref, credentials, progress):
        registry = ref.split('/')[0]
        auth = {'username': credentials.get('username', ''), 'password': credentials.get('token', ''), 'serveraddress': registry}
        header = base64.urlsafe_b64encode(json.dumps(auth).encode()).decode()
        layers = {}
        with self.client.stream('POST', '/images/create', params={'fromImage': ref},
                                headers={'X-Registry-Auth': header}, timeout=httpx.Timeout(60, read=180)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if len(line) > 128 * 1024:
                    raise ValueError('Invalid registry progress response.')
                if not line:
                    continue
                event = json.loads(line)
                if 'error' in event:
                    raise ValueError('The registry could not supply the verified image.')
                detail = event.get('progressDetail', {})
                if event.get('status') == 'Downloading' and detail.get('total'):
                    layers[event['id']] = (detail.get('current', 0), detail['total'])
                    progress(sum(item[0] for item in layers.values()), sum(item[1] for item in layers.values()))
        image = self.request('GET', '/images/' + quote(ref, safe='') + '/json')
        if ref not in image.get('RepoDigests', []):
            raise ValueError('Downloaded image does not match its release digest.')

