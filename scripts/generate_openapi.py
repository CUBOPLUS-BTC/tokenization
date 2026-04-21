import sys, os, json
service = sys.argv[1]
os.environ['PYTHONPATH'] = ';' + os.environ.get('PYTHONPATH', '')

if service == 'auth':
    from auth.main import app
elif service == 'wallet':
    from wallet.main import app
elif service == 'tokenization':
    from tokenization.main import app
elif service == 'marketplace':
    from marketplace.main import app
elif service == 'nostr':
    from nostr.main import app
elif service == 'admin':
    from admin.main import app

os.makedirs('docs/api', exist_ok=True)
with open(f'docs/api/{service}.json', 'w') as f:
    json.dump(app.openapi(), f, indent=2)
print(f'Generated {service}.json')
