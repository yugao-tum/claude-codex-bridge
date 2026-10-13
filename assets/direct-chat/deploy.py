"""Deploy the independent Linux runtime and nonsecret settings through SSH."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from config import load_config, remote_config

BASE = Path(__file__).resolve().parent
CODE_FILES = ('config.py', 'remote_runner.py', 'mcp_stdio.py')
BOOTSTRAP = r'''
from pathlib import Path
import hashlib,json,os,sys,time
os.umask(0o077)
payload=json.load(sys.stdin)
root=Path(payload['remote_root'])
if not root.is_absolute() or '..' in root.parts or root == Path('/'):
 raise ValueError('invalid_deployment_root')
if any(path.is_symlink() for path in (root,*root.parents)):
 raise ValueError('deployment_root_must_not_be_symlink')
files=payload['files']
if set(files) != {'config.py','remote_runner.py','mcp_stdio.py','runtime-config.json'}:
 raise ValueError('invalid_deployment_files')
settings=json.loads(files['runtime-config.json'])
if settings['remote_root'] != str(root):
 raise ValueError('deployment_root_mismatch')
for name,content in files.items():
 if name.endswith('.py'): compile(content,name,'exec')
root.mkdir(mode=0o700,parents=True,exist_ok=True)
out={}
for name,content in files.items():
 target=root/name
 if target.is_symlink(): raise ValueError('deployment_target_must_not_be_symlink')
 data=content.encode('utf-8')
 if target.exists() and target.read_bytes()!=data:
  backups=root/'deployment-backups'; backups.mkdir(mode=0o700,exist_ok=True)
  (backups/(str(time.time_ns())+'-'+name)).write_bytes(target.read_bytes())
 temp=root/(name+'.'+str(time.time_ns())+'.tmp')
 with temp.open('xb') as stream:
  stream.write(data); stream.flush(); os.fsync(stream.fileno())
 temp.chmod(0o600)
 os.replace(temp,target)
 out[name]=hashlib.sha256(target.read_bytes()).hexdigest()
print(json.dumps({'root':str(root),'sha256':out}))
'''

def deployment_payload(settings):
    files = {name: (BASE / name).read_text(encoding='utf-8') for name in CODE_FILES}
    # Never deploy local workspace paths, SSH configuration or authentication.
    files['runtime-config.json'] = json.dumps(remote_config(settings), ensure_ascii=False, indent=2)
    return {'remote_root': settings['remote_root'], 'files': files}


def deploy(settings):
    payload = deployment_payload(settings)
    argv = settings['ssh_argv'] + [shlex.join(settings['remote_prefix'] + ['-c', BOOTSTRAP])]
    completed = subprocess.run(argv, input=json.dumps(payload), capture_output=True,
                               text=True, encoding='utf-8', timeout=55)
    if completed.returncode:
        raise RuntimeError('SSH deployment failed (exit %s); verify your SSH/container configuration.' % completed.returncode)
    result = json.loads(completed.stdout)
    if result.get('root') != settings['remote_root']:
        raise RuntimeError('Deployment root receipt did not match.')
    for name, value in payload['files'].items():
        if result.get('sha256', {}).get(name) != hashlib.sha256(value.encode('utf-8')).hexdigest():
            raise RuntimeError('Deployment SHA-256 receipt did not match.')
    (BASE / 'deployment-receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    try:
        deploy(load_config())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from None
    print('Direct runtime deployed; all four SHA-256 receipts matched. No model was called.')
