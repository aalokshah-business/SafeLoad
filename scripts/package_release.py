"""Package the Windows bundle, matching source and licenses for public releases."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import sys
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--version', required=True)
args = parser.parse_args()
if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
    parser.error('Version must use MAJOR.MINOR.PATCH.')
root = Path(__file__).resolve().parents[1]
bundle = root / 'dist' / 'SafeLoad'
if not (bundle / 'SafeLoad.exe').is_file():
    parser.error('Build dist/SafeLoad/SafeLoad.exe before packaging.')
source = bundle / 'Source'
source.mkdir(exist_ok=True)
for name in ('app.py', 'scanner.py', 'requirements.txt', 'README.md', 'LICENSE', 'install.ps1',
             'Start SafeLoad.cmd', 'Build Windows App.cmd'):
    shutil.copy2(root / name, source / name)
for name in ('tests', 'scripts', 'assets'):
    shutil.copytree(root / name, source / name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
for name in ('README.md', 'LICENSE', 'install.ps1'):
    shutil.copy2(root / name, bundle / name)
(bundle / 'VERSION').write_text(args.version + '\n', encoding='ascii')
packages = Path(sys.prefix) / 'Lib' / 'site-packages'
for info in packages.glob('*.dist-info'):
    for path in info.rglob('*'):
        if path.is_file() and any(word in path.name.lower() for word in ('license', 'copying', 'notice')):
            target = bundle / 'ThirdPartyLicenses' / path.relative_to(packages)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
archive = root / 'dist' / 'SafeLoad-Windows-x64.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted(bundle.rglob('*')):
        if path.is_file():
            z.write(path, path.relative_to(bundle.parent))
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.zip.sha256').write_text(checksum + '\n', encoding='ascii')
print(f'{archive} ({archive.stat().st_size / 1048576:.1f} MiB)')
print(f'SHA-256: {checksum}')
