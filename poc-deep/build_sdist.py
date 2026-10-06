"""Build the probe sdist without needing setuptools locally.

An sdist is just a gzipped tar whose single top-level directory is named
{name}-{version} and contains setup.py plus a PKG-INFO. pip unpacks it and runs
setup.py, which is exactly the code path the report is about.

    python build_sdist.py     -> dist/zzbuildprobe-1.3.0.tar.gz
"""
import io
import os
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME, VERSION = 'zzbuildprobe', '1.3.0'
ROOT = '%s-%s' % (NAME, VERSION)

MEMBERS = [
    'setup.py',
    'setup.cfg',
    'PKG-INFO',
    'zzbuildprobe/__init__.py',
    'zzbuildprobe/build_evidence.py',
]

PKG_INFO = 'Metadata-Version: 2.1\nName: %s\nVersion: %s\n' % (NAME, VERSION)


def add(tar, arcname, data):
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 1700000000
    info.uid = info.gid = 0
    info.uname = info.gname = 'root'
    tar.addfile(info, io.BytesIO(data))


def main():
    os.makedirs(os.path.join(HERE, 'dist'), exist_ok=True)
    out = os.path.join(HERE, 'dist', '%s-%s.tar.gz' % (NAME, VERSION))

    with tarfile.open(out, 'w:gz') as tar:
        d = tarfile.TarInfo(ROOT)
        d.type = tarfile.DIRTYPE
        d.mode = 0o755
        d.mtime = 1700000000
        tar.addfile(d)
        for rel in MEMBERS:
            if rel == 'PKG-INFO':
                add(tar, '%s/%s' % (ROOT, rel), PKG_INFO.encode())
                continue
            path = os.path.join(HERE, rel)
            if not os.path.exists(path):
                print('skip (missing): %s' % rel)
                continue
            with open(path, 'rb') as f:
                add(tar, '%s/%s' % (ROOT, rel), f.read())

    print('built: %s (%d bytes)' % (out, os.path.getsize(out)))
    with tarfile.open(out) as tar:
        for m in tar.getmembers():
            print('   ', m.name)


if __name__ == '__main__':
    main()
