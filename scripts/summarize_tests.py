"""CI Summary에 고정 상태와 테스트 파일 위치만 표시한다. 예외/환경변수 없음."""
import json
import os
from pathlib import Path
import sys


def summary(path):
    if not path.exists():
        return 'Test report missing: dependency/Docker/collection setup did not complete. Check the failed job step.\n'
    data = json.loads(path.read_text(encoding='utf-8'))
    counts = data['counts']
    lines = [f"Passed: {counts['passed']}; Failed: {counts['failed']}; Not run/skipped: {counts['skipped']}"]
    if 'exit_code' in data:
        lines.append(f"Runner exit code: {data['exit_code']} (nonzero also includes setup/collection errors).")
    if 'runner_file' in data:
        lines.append(f"Source: `{data['runner_file']}`")
    tests = data['tests']
    items = tests.items() if isinstance(tests, dict) else ((r['test'], r) for r in tests)
    for name, row in items:
        if row['status'] != 'passed':
            lines.append(f"- {row['status']}: `{name}` ({row.get('category', row.get('phase', ''))})")
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    content = summary(Path(sys.argv[1]))
    print(content)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(content)
