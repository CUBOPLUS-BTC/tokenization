import re

path = 'services/admin/main.py'
with open(path, 'r') as f:
    text = f.read()

text = re.sub(r'# -*\n# 7\.4  Create Course\n# -*\n\n\n@app\.post.*?return CourseResponse\(course=_course_out\(row\)\)\.model_dump\(mode="json"\)\n\n', '', text, flags=re.DOTALL)
text = text.replace('        "/courses",\n', '')

with open(path, 'w') as f:
    f.write(text)
