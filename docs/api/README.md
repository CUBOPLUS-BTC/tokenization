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