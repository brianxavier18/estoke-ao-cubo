# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :---: |
| `4.3.x` | Yes |
| `< 4.3` | No |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub Issues.

Instead, report them privately via GitHub Security Advisories (**Security → Advisories → Report a vulnerability**) or contact the maintainer directly. Include a clear description of the issue and steps to reproduce.

## Security Architecture Highlights

- Local SQLite database encryption at rest (Fernet AES-128 + Windows DPAPI key protection + NTFS ACL restrictions).
- Password hashing using `scrypt` / PBKDF2 with automatic legacy hash migration.
- Embedded REST API protected with Bearer session tokens, RBAC role checks, and per-IP rate limiting.
