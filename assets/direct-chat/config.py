"""Explicit, credential-free configuration for the local and remote runtimes."""
import argparse
import json
from pathlib import Path, PurePosixPath

BASE = Path(__file__).resolve().parent
REMOTE_FIELDS = ('remote_root', 'remote_home', 'remote_claude_binary', 'model', 'effort', 'timeout_seconds')
LOCAL_FIELDS = ('workspace_root', 'default_workspace', 'ssh_argv', 'remote_prefix', 'port')


def remote_config(value):
    if not isinstance(value, dict) or any(key not in value for key in REMOTE_FIELDS):
        raise ValueError('missing_remote_configuration')
    result = {key: value[key] for key in REMOTE_FIELDS}
    for key in ('remote_root', 'remote_home', 'remote_claude_binary'):
        item = result[key]
        if not isinstance(item, str) or not item.startswith('/') or '\\' in item or '\x00' in item:
            raise ValueError('remote_paths_must_be_absolute_posix_paths')
        path = PurePosixPath(item)
        if '..' in path.parts or str(path) == '/':
            raise ValueError('invalid_remote_path')
        result[key] = str(path)
    root, home = PurePosixPath(result['remote_root']), PurePosixPath(result['remote_home'])
    # Deployment must target a dedicated directory, never an authentication/project directory.
    if root == home or root in home.parents or root == home / '.claude' or root.is_relative_to(home / '.claude' / 'projects'):
        raise ValueError('remote_root_must_be_a_dedicated_direct_directory')
    if len((str(root) + '/sockets/' + '0' * 32 + '.sock').encode('utf-8')) > 103:
        raise ValueError('remote_root_too_long_for_unix_socket')
    model = result['model']
    if not isinstance(model, str) or not model or any(char.isspace() for char in model) or '\x00' in model or model.startswith('-') or model == 'REPLACE_WITH_EXACT_AVAILABLE_MODEL_ID':
        raise ValueError('set_an_exact_available_model_id')
    if result['effort'] not in ('low', 'medium', 'high', 'max'):
        raise ValueError('effort_must_be_low_medium_high_or_max')
    if type(result['timeout_seconds']) is not int or not 1 <= result['timeout_seconds'] <= 900:
        raise ValueError('timeout_must_be_integer_1_to_900')
    return result


def load_config(path=None):
    path = Path(path) if path is not None else BASE / 'config.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise ValueError('Copy config.example.json to config.json and fill your settings first.') from None
    result = remote_config(value)
    if any(key not in value for key in LOCAL_FIELDS):
        raise ValueError('missing_local_configuration')
    unknown = set(value) - set(REMOTE_FIELDS + LOCAL_FIELDS)
    if unknown:
        raise ValueError('unsupported_configuration_field')
    for key in ('workspace_root', 'default_workspace'):
        item = value[key]
        if not isinstance(item, str) or not item or '\x00' in item or not Path(item).is_absolute():
            raise ValueError('local_workspace_paths_must_be_absolute')
        result[key] = Path(item).resolve()
    if not result['default_workspace'].is_relative_to(result['workspace_root']):
        raise ValueError('default_workspace_must_be_inside_workspace_root')
    if result['default_workspace'].is_relative_to(BASE):
        raise ValueError('default_workspace_must_be_outside_direct_runtime')
    for key in ('ssh_argv', 'remote_prefix'):
        argv = value[key]
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item or any(c in item for c in '\x00\r\n') for item in argv):
            raise ValueError('connection_argv_must_be_nonempty_string_arrays')
        result[key] = list(argv)
    if any(item in ('-n', '-f') for item in result['ssh_argv']):
        raise ValueError('ssh_must_keep_stdin_open')
    if type(value['port']) is not int or not 1024 <= value['port'] <= 65535:
        raise ValueError('port_must_be_integer_1024_to_65535')
    result['port'] = value['port']
    return result


def load_remote_config(root):
    value = remote_config(json.loads((Path(root) / 'runtime-config.json').read_text(encoding='utf-8')))
    if Path(value['remote_root']).resolve() != Path(root).resolve():
        raise ValueError('deployed_root_does_not_match_configuration')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', action='store_true', help='Print only the validated local listen port.')
    args = parser.parse_args()
    try:
        config = load_config()
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(config['port'] if args.port else 'Configuration is valid; no connection was made.')
