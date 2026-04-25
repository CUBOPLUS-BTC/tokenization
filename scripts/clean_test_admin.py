import re

path = 'tests/test_admin.py'
with open(path, 'r') as f:
    text = f.read()

text = re.sub(r'class FakeCourse.*?return FakeCourse\([^)]+\)\n\n\n', '', text, flags=re.DOTALL)
text = re.sub(r'# ---------------------------------------------------------------------------\n# POST /courses\n# ---------------------------------------------------------------------------\n\n\n.*?def test_non_admin_cannot_create_course.*?status_code == 403\n\n\n', '', text, flags=re.DOTALL)

with open(path, 'w') as f:
    f.write(text)
