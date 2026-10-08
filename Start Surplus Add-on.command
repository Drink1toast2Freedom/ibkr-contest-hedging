#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'The project Python environment is missing. Set up the main app first.'
  exit 1
fi
print 'Surplus research: http://127.0.0.1:8770 — no broker execution'
if /usr/bin/curl --silent --fail --max-time 1 'http://127.0.0.1:8770/api/state' > /dev/null; then
  open 'http://127.0.0.1:8770'
  exit 0
fi
(.venv/bin/python - <<'PY'
import subprocess,time,urllib.request
for _ in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8770/api/state',timeout=1) as response:
            if response.status==200:
                subprocess.run(['/usr/bin/open','http://127.0.0.1:8770'],check=False)
                break
    except OSError:
        pass
    time.sleep(0.5)
PY
) &
exec .venv/bin/python -m surplus_addon serve
