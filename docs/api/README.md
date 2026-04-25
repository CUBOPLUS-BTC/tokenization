# REST API Documentation

This directory contains the statically generated OpenAPI (Swagger) specifications for each backend service. These artifacts accurately describe every available route, its request and response schema, expected headers, and authentication methods.

- [Auth API](auth.json)
- [Wallet API](wallet.json)
- [Tokenization API](tokenization.json)
- [Marketplace API](marketplace.json)
- [Nostr API](nostr.json)
- [Admin API](admin.json)

## How to use

You can visualize these `.json` files using tools such as:
- **Swagger UI**: You can drag and drop these JSON files into [editor.swagger.io](https://editor.swagger.io).
- **Postman**: These can be imported directly into a Postman workspace to autogenerate API Client collections.
- **Redoc**

## Updating these schemas

If you make modifications to any FastAPI route, model, or configuration in `services/`, you can regenerate these OpenAPI specifications using the following command from the root directory:

```powershell
$env:PYTHONPATH="C:\absolute\path\to\tokenization\services"
foreach ($s in @('auth', 'wallet', 'tokenization', 'marketplace', 'nostr', 'admin')) {
    python scripts/generate_openapi.py $s
}
```

## Command list (manual generation)

Run these commands from the repository root to generate each schema individually:

```powershell
python scripts/generate_openapi.py auth
python scripts/generate_openapi.py admin
python scripts/generate_openapi.py marketplace
python scripts/generate_openapi.py tokenization
python scripts/generate_openapi.py wallet
python scripts/generate_openapi.py nostr
```

If you get an error related to form data, install the missing dependency once:

```powershell
python -m pip install python-multipart
```

## Copy generated files to Desktop (Windows)

After generating JSON files, you can copy them to a Desktop folder with:

```powershell
$target = "$HOME\Desktop\tokenization-openapi-json"
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item docs/api/auth.json, docs/api/admin.json, docs/api/marketplace.json, docs/api/tokenization.json, docs/api/wallet.json, docs/api/nostr.json -Destination $target -Force
```
