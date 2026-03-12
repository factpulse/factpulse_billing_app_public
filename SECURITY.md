# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, send an email to: **contact@factpulse.fr**

Please include:

- A description of the vulnerability
- Steps to reproduce the issue
- The potential impact
- Any suggested fix (if applicable)

## Response Timeline

- **Acknowledgement**: within 48 hours of your report.
- **Initial assessment**: within 5 business days.
- **Fix and disclosure**: we aim to release a patch within 30 days for confirmed vulnerabilities. We will coordinate disclosure timing with you.

## Disclosure Policy

- We follow [coordinated vulnerability disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure).
- We will credit reporters in the release notes (unless you prefer to remain anonymous).
- We ask that you do not publicly disclose the vulnerability until a fix has been released.

## Scope

The following are in scope:

- The FactPulse Billing App codebase
- Authentication and authorization mechanisms
- Data exposure or injection vulnerabilities
- Dependencies with known CVEs that affect this project

Out of scope:

- Vulnerabilities in third-party services (FactPulse API, MinIO, PostgreSQL) — please report those to the respective projects.
- Social engineering attacks.
- Denial of service attacks.
