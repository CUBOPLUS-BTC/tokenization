import sys, os, json

service = sys.argv[1]

# Ensure the services/ directory is on the import path
_services_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'services')
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

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
